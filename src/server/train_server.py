"""
train_server.py — WebSocket bridge between the trainer UI and the PPO trainer.

This file is mostly plumbing. The interesting domain logic lives in rules.py.

Wire protocol (JSON over WebSocket):

  Client → Server
    { "type": "configure",
      "target_rounds": 10,
      "difficulty": "medium",
      "total_timesteps": 20000,
      "rewards": { ... overrides for rules.REWARDS keys ... } }
    { "type": "start" }
    { "type": "pause" }
    { "type": "resume" }
    { "type": "reset" }                          # drop the model
    { "type": "generate", "prefix": "..." }      # roll out from a user prefix

  Server → Client
    { "type": "ready",
      "vocab_sizes": {low,medium,high},
      "reward_keys": [...] }
    { "type": "status", "phase": "idle|training|paused|done|error", "msg": "..." }
    { "type": "progress", "step": N, "total": M,
      "ep_reward_mean": X, "ep_count": K, "best_reward": Y }
    { "type": "best_pattern", "pattern": "...", "reward": Y }
    { "type": "generated", "pattern": "...", "ok": bool,
      "n_stitches": N, "error": "..." }
    { "type": "error", "msg": "..." }
"""

from __future__ import annotations

import argparse
import asyncio
import json
import queue
import sys
import threading
import traceback
from collections import OrderedDict
from pathlib import Path
from typing import Optional

import numpy as np

# Make sibling modules importable.
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from parser_client import ParserClient                          # noqa: E402
from crochet_env import CrochetTokenEnv, EnvConfig, VOCABS      # noqa: E402
import rules                                                    # noqa: E402

try:
    import websockets
except ImportError as e:
    print("ERROR: pip install websockets", file=sys.stderr)
    raise SystemExit(1) from e

try:
    from stable_baselines3 import PPO
    from stable_baselines3.common.callbacks import BaseCallback
except ImportError as e:
    print("ERROR: pip install stable-baselines3 torch", file=sys.stderr)
    raise SystemExit(1) from e


# ---------------------------------------------------------------------------
# Callback: stream training metrics back to the browser.
# ---------------------------------------------------------------------------
class StreamingCallback(BaseCallback):
    def __init__(self, post, total_timesteps: int, control: "TrainerControl"):
        super().__init__()
        self._post = post
        self._total = max(1, int(total_timesteps))
        self._control = control
        self._best_reward = float("-inf")
        self._best_pattern = ""
        self._ep_rewards: list[float] = []
        self._last_announced_step = 0

    def _on_step(self) -> bool:
        if self._control.stop_requested:
            return False
        while self._control.pause_requested and not self._control.stop_requested:
            self._control.pause_event.wait(timeout=0.1)
        if self._control.stop_requested:
            return False

        # Episode-end bookkeeping. Read previous-episode snapshots taken on
        # auto-reset.
        dones = self.locals.get("dones", [])
        envs = self.training_env.envs if hasattr(self.training_env, "envs") else []
        for i, done in enumerate(dones):
            if not done:
                continue
            inner = envs[i].unwrapped if i < len(envs) else None
            if inner is None:
                continue
            ep_r = float(getattr(inner, "previous_episode_reward", 0.0))
            best_pat = getattr(inner, "previous_episode_pattern", "") or ""
            self._ep_rewards.append(ep_r)
            if ep_r > self._best_reward and best_pat:
                self._best_reward = ep_r
                self._best_pattern = best_pat
                self._post({
                    "type": "best_pattern",
                    "pattern": best_pat,
                    "reward": ep_r,
                })

        if self.num_timesteps - self._last_announced_step >= 500:
            self._last_announced_step = self.num_timesteps
            recent = self._ep_rewards[-50:]
            mean_r = float(np.mean(recent)) if recent else 0.0
            self._post({
                "type": "progress",
                "step": int(self.num_timesteps),
                "total": self._total,
                "ep_reward_mean": mean_r,
                "ep_count": len(self._ep_rewards),
                "best_reward": (self._best_reward
                                if self._best_reward != float("-inf") else 0.0),
            })
        return True


class TrainerControl:
    def __init__(self):
        self.pause_requested = False
        self.stop_requested = False
        self.pause_event = threading.Event()
        self.pause_event.set()


# ---------------------------------------------------------------------------
# The server itself.
# ---------------------------------------------------------------------------
class TrainServer:
    def __init__(self, host: str, port: int):
        self._host = host
        self._port = port
        self._clients: set = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        self._parser = ParserClient()

        self._env_cfg = EnvConfig()
        self._total_timesteps = 10_000
        self._model: Optional[PPO] = None
        self._trainer_thread: Optional[threading.Thread] = None
        self._control = TrainerControl()
        self._msg_queue: "queue.Queue[dict]" = queue.Queue()

        # Single-session ownership. The trainer is shared, so only one client
        # may drive it at a time. Whoever starts training becomes the owner;
        # everyone else gets a polite "busy" reply until the owner is released
        # (training finishes/errors, owner resets, or owner disconnects).
        self._owner = None

        # Per-user trained models, keyed by a session id the browser sends.
        # Training is still one-at-a-time (CPU-bound), but each user's trained
        # model is kept here so one person training doesn't destroy another
        # person's result — they can still generate from their own model.
        # In-memory only (lost on restart), capped to MAX_SESSIONS (oldest
        # evicted) so a 6 GB box can't be exhausted by parked models.
        # Each entry: {"model", "vocab_sig", "env_cfg", "total_timesteps"}.
        self._sessions: "OrderedDict[str, dict]" = OrderedDict()
        self.MAX_SESSIONS = 5

    # ----- broadcasting -----------------------------------------------------

    def _post_from_thread(self, msg: dict) -> None:
        """Trainer thread → asyncio loop. Safe to call from any thread."""
        self._msg_queue.put(msg)

    async def _drain_queue(self) -> None:
        while True:
            try:
                msg = await asyncio.get_running_loop().run_in_executor(
                    None, self._msg_queue.get, True, 0.5
                )
            except queue.Empty:
                continue
            except Exception:
                await asyncio.sleep(0.1)
                continue
            if not self._clients:
                continue
            payload = json.dumps(msg)
            # Training updates belong to the client that started the session.
            # If there's an owner, send only to them so bystanders don't see
            # someone else's progress. Messages with no owner (or if the owner
            # has gone) fall back to broadcasting to everyone.
            if self._owner is not None and self._owner in self._clients:
                targets = [self._owner]
            else:
                targets = list(self._clients)
            dead = []
            for ws in targets:
                try:
                    await ws.send(payload)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self._clients.discard(ws)

    # ----- websocket handler ------------------------------------------------

    async def _handle_client(self, ws):
        self._clients.add(ws)
        try:
            await ws.send(json.dumps({
                "type": "ready",
                "vocab_sizes": {k: len(v) for k, v in VOCABS.items()},
                "reward_keys": list(rules.REWARDS.keys()),
                "stitch_menu": rules.get_stitch_menu(),
            }))
            async for raw in ws:
                await self._dispatch(ws, raw)
        except Exception:
            traceback.print_exc()
        finally:
            self._clients.discard(ws)
            # If the owner disconnected, free the session. If they were
            # mid-training, ask the run to stop so the box isn't tied up.
            if self._owner is ws:
                self._owner = None
                if (self._trainer_thread is not None
                        and self._trainer_thread.is_alive()):
                    self._control.stop_requested = True
                    self._control.pause_event.set()

    async def _dispatch(self, ws, raw):
        try:
            msg = json.loads(raw)
            kind = msg.get("type")
            sid = str(msg.get("sid") or "")

            # Gate the actions that drive the shared trainer. Only the current
            # owner may run them; if someone else holds the session, reply busy.
            session_actions = ("configure", "start", "pause", "resume",
                               "reset", "generate")
            if (kind in session_actions
                    and self._owner is not None
                    and self._owner in self._clients
                    and self._owner is not ws):
                await ws.send(json.dumps({
                    "type": "status", "phase": "busy",
                    "msg": "Someone else is training right now — "
                           "please try again in a bit."}))
                return

            if kind == "configure":
                self._claim(ws)
                self._configure(msg)
                await ws.send(json.dumps({"type": "status", "phase": "idle",
                                          "msg": "configured"}))
            elif kind == "start":
                self._claim(ws)
                self._train_sid = sid          # whose model this run produces
                self._start_training()
            elif kind == "pause":
                self._control.pause_requested = True
                self._control.pause_event.clear()
                await ws.send(json.dumps({"type": "status", "phase": "paused",
                                          "msg": "paused"}))
            elif kind == "resume":
                self._control.pause_requested = False
                self._control.pause_event.set()
                await ws.send(json.dumps({"type": "status", "phase": "training",
                                          "msg": "resumed"}))
            elif kind == "reset":
                self._reset_model()
                self._sessions.pop(sid, None)  # forget this user's saved model
                self._owner = None
                await ws.send(json.dumps({"type": "status", "phase": "idle",
                                          "msg": "model reset"}))
            elif kind == "generate":
                result = self._generate(msg.get("prefix") or "", sid)
                await ws.send(json.dumps({"type": "generated", **result}))
            else:
                await ws.send(json.dumps({"type": "error",
                                          "msg": f"unknown-type:{kind}"}))
        except Exception as e:
            traceback.print_exc()
            await ws.send(json.dumps({
                "type": "error", "msg": f"{type(e).__name__}: {e}"
            }))

    def _claim(self, ws) -> None:
        """Mark this client as the session owner if no one else holds it."""
        if self._owner is None or self._owner not in self._clients:
            self._owner = ws

    # ----- training control -------------------------------------------------

    def _configure(self, msg: dict) -> None:
        target = int(msg.get("target_rounds") or 4)
        difficulty = str(msg.get("difficulty") or "medium")
        steps = int(msg.get("total_timesteps") or 10_000)
        if difficulty not in VOCABS:
            difficulty = "medium"

        # Custom stitch selection from the menu (list of menu ids / tokens).
        # When present it overrides difficulty for the body vocabulary.
        custom_bodies = rules.resolve_custom_bodies(
            msg.get("custom_bodies") or [])

        # Reward overrides: accept any key the UI sends that's also in
        # rules.REWARDS. Unknown keys are silently dropped — that way, edits
        # to rules.py can add new keys without breaking the protocol.
        overrides: dict = {}
        for k, v in (msg.get("rewards") or {}).items():
            if k in rules.REWARDS:
                try:
                    overrides[k] = float(v)
                except (TypeError, ValueError):
                    pass

        self._env_cfg = EnvConfig(
            target_rounds=max(2, min(20, target)),
            difficulty=difficulty,
            custom_bodies=custom_bodies,
            reward_overrides=overrides,
        )
        self._total_timesteps = max(1_000, min(2_000_000, steps))

    def _start_training(self) -> None:
        if self._trainer_thread is not None and self._trainer_thread.is_alive():
            self._post_from_thread({"type": "status", "phase": "training",
                                    "msg": "already training"})
            return
        self._control = TrainerControl()
        self._trainer_thread = threading.Thread(
            target=self._train_run, name="ppo-trainer", daemon=True)
        self._trainer_thread.start()

    def _train_run(self) -> None:
        try:
            self._post_from_thread({"type": "status", "phase": "training",
                                    "msg": "spinning up PPO"})
            self._parser.start()
            env = CrochetTokenEnv(self._parser, self._env_cfg)

            # The observation/action space size depends on the body
            # vocabulary the env was built with. With a custom stitch
            # selection that set can change between runs even at the same
            # difficulty, so reusing the old model would raise "Observation
            # spaces do not match". We key the rebuild on a signature of the
            # actual bodies (custom selection if any, else the difficulty),
            # and discard the model whenever that signature changes.
            vocab_sig = (tuple(self._env_cfg.custom_bodies)
                         if self._env_cfg.custom_bodies
                         else ("diff", self._env_cfg.difficulty))
            if (self._model is not None
                    and getattr(self, "_model_vocab_sig", None) != vocab_sig):
                self._model = None

            if self._model is None:
                self._model = PPO(
                    "MlpPolicy", env,
                    n_steps=256,
                    batch_size=64,
                    n_epochs=4,
                    learning_rate=3e-4,
                    gamma=0.95,
                    gae_lambda=0.9,
                    ent_coef=0.05,   # higher → more exploration of the vocab
                    verbose=0,
                    policy_kwargs={"net_arch": [64, 64]},
                )
            else:
                self._model.set_env(env)

            # Remember which vocabulary signature this model is valid for.
            self._model_vocab_sig = vocab_sig

            cb = StreamingCallback(self._post_from_thread,
                                   self._total_timesteps, self._control)
            self._model.learn(total_timesteps=self._total_timesteps,
                              callback=cb, reset_num_timesteps=False)

            if self._control.stop_requested:
                self._post_from_thread({"type": "status", "phase": "idle",
                                        "msg": "training stopped"})
            else:
                # Save this user's freshly trained model under their session id
                # so a later trainer can't destroy it. Keep at most
                # MAX_SESSIONS, evicting the oldest.
                sid = getattr(self, "_train_sid", "") or ""
                if sid and self._model is not None:
                    self._sessions[sid] = {
                        "model": self._model,
                        "vocab_sig": getattr(self, "_model_vocab_sig", None),
                        "env_cfg": self._env_cfg,
                        "total_timesteps": self._total_timesteps,
                    }
                    self._sessions.move_to_end(sid)
                    while len(self._sessions) > self.MAX_SESSIONS:
                        self._sessions.popitem(last=False)  # drop oldest
                self._post_from_thread({"type": "status", "phase": "done",
                                        "msg": "training complete"})
        except Exception as e:
            traceback.print_exc()
            self._post_from_thread({"type": "status", "phase": "error",
                                    "msg": f"{type(e).__name__}: {e}"})
        finally:
            # Training is over (done, stopped, or errored) — free the session
            # so the next person can train. The trained model itself is kept
            # until someone resets, so the owner can still generate from it.
            self._owner = None

    def _reset_model(self) -> None:
        self._control.stop_requested = True
        self._control.pause_event.set()
        if self._trainer_thread is not None:
            self._trainer_thread.join(timeout=5.0)
        self._trainer_thread = None
        self._model = None

    # ----- generation -------------------------------------------------------

    def _generate(self, prefix: str, sid: str = "") -> dict:
        """Roll out the trained policy for one full episode.

        Uses the caller's own saved model (looked up by session id), so one
        user generating isn't affected by someone else having trained since.
        Falls back to the shared working model if no session is found (e.g.
        the user who just trained, before anything else happened).

        The procedural env doesn't accept token prefixes (every pattern is
        constructed from the agent's body+corner choices), so we ignore the
        prefix argument. If you want prefix support back, you'd need to add
        a "seed N rounds from text" hook to the env.
        """
        sess = self._sessions.get(sid) if sid else None
        if sess is not None:
            model = sess["model"]
            env_cfg = sess["env_cfg"]
            self._sessions.move_to_end(sid)   # mark recently used
        else:
            model = self._model
            env_cfg = self._env_cfg

        if model is None:
            return {"ok": False,
                    "error": "no trained model yet — start training first"}

        self._parser.start()

        target = env_cfg.target_rounds

        # Each round now takes FOUR agent steps (body, corner, corner-template,
        # side-template), and any out-of-phase pick is skipped without advancing
        # the phase — so we budget generously (4 steps/round plus a large retry
        # slack) and try a few independent rollouts, keeping the first one that
        # both parses AND reaches the requested round count. This is what makes
        # "5 rows" actually produce 5 rows instead of stalling early.
        max_steps = target * 4 + 200
        best = None

        for _attempt in range(8):
            env = CrochetTokenEnv(self._parser, env_cfg)
            obs, _ = env.reset()
            for _ in range(max_steps):
                action, _ = model.predict(obs, deterministic=False)
                obs, _, term, trunc, _ = env.step(int(action))
                if term or trunc:
                    break

            pattern = env.last_episode_pattern or env.current_pattern
            if not pattern.strip():
                continue

            verdict = self._parser.evaluate(pattern)
            rounds = self._count_rounds(pattern)
            candidate = {
                "ok": bool(verdict.ok),
                "pattern": pattern,
                "n_stitches": int(verdict.n_stitches),
                "rounds": rounds,
                "error": str(verdict.error) if not verdict.ok else "",
            }
            # Perfect hit: parses and has exactly the requested rounds.
            if candidate["ok"] and rounds == target:
                return candidate
            # Otherwise remember the best-so-far (prefer ok + closest to target).
            if best is None or _better(candidate, best, target):
                best = candidate

        return best or {"ok": False, "pattern": "",
                        "error": "model produced nothing"}

    @staticmethod
    def _count_rounds(pattern: str) -> int:
        """Rounds = number of distinct row first-stitch anchors. Every round's
        turning chain is labelled st_{N}_1 (and the closer ss@st_{N}_1 or
        ss@st_{N}_<n> references it), so the set of row numbers N in any
        st_{N}_<k> label gives the round count."""
        import re
        return len(set(re.findall(r"st_(\d+)_\d+", pattern)))

    # ----- entry point ------------------------------------------------------

    async def serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        print(f"crochet trainer listening on ws://{self._host}:{self._port}",
              file=sys.stderr)
        drain_task = asyncio.create_task(self._drain_queue())
        async with websockets.serve(self._handle_client, self._host, self._port):
            try:
                await asyncio.Future()
            finally:
                drain_task.cancel()


def _better(cand: dict, cur: dict, target: int) -> bool:
    """Rank generation attempts: prefer parseable, then closest round count to
    the target, then more stitches as a tiebreak."""
    def key(c):
        return (1 if c.get("ok") else 0,
                -abs(int(c.get("rounds", 0)) - target),
                int(c.get("n_stitches", 0)))
    return key(cand) > key(cur)


def main() -> None:
    ap = argparse.ArgumentParser(description="Crochet RL trainer WebSocket server")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()

    asyncio.run(TrainServer(args.host, args.port).serve())


if __name__ == "__main__":
    main()