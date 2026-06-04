# Hosting Entangled so others can test it

Your app has two parts that BOTH need to run on the server:

1. **The website** — all the HTML/JS/CSS/WASM (served as static files).
2. **The trainer** — `server/train_server.py`, a Python WebSocket server that
   runs PPO training (torch + stable-baselines3) and spawns `node
   server/parser_worker.js`.

Because of part 2, you cannot use a free static host (GitHub Pages, Netlify,
Vercel). You need a small server (VPS) that can run Python **and** Node.

---

## What to rent

Any small Linux VPS works. Good cheap options (~€4-6/month):

- Hetzner CX22  (2 vCPU / 4 GB RAM)  — cheapest solid choice
- DigitalOcean Basic Droplet (2 GB+; 4 GB is safer for torch)

Pick **Ubuntu 24.04**. 4 GB RAM is recommended because torch + a training run
is memory-hungry. Training is CPU-bound and runs one job at a time well; many
simultaneous "Train" clicks on a tiny box will be slow — fine for a handful of
testers.

---

## Setup (do this once)

1. Copy this whole folder to the server, e.g. with scp:

       scp -r ./src  you@your-server-ip:~/entangled

2. SSH in and run the setup script:

       ssh you@your-server-ip
       cd ~/entangled
       bash deploy_setup.sh

   This installs Python, Node, and all Python dependencies (takes a few
   minutes — torch is ~2 GB).

---

## Run it

       bash run_server.sh

This starts the website on port 80 and the trainer on port 8765.
Open the firewall for both ports (most VPS dashboards have a firewall panel;
allow inbound TCP 80 and 8765).

Then anyone can visit:

       http://your-server-ip/sandbox.html
       http://your-server-ip/train_simplified.html

The page auto-detects the server address, so the trainer connects to your
server automatically — visitors don't configure anything.

To keep it running after you log out, use `tmux` or `screen`:

       sudo apt-get install -y tmux
       tmux new -s entangled
       bash run_server.sh
       # detach with: Ctrl-b then d
       # reattach later with: tmux attach -t entangled

---

## Optional: a real domain + HTTPS

If you point a domain at the server, browsers on `https://` pages block
plain `ws://`. You'd then serve the trainer over `wss://`. Easiest path is to
put nginx + a free Let's Encrypt certificate in front and proxy `/ws` to port
8765. Ask if you want that config — it's a bit more setup. For "friends testing
at home" over plain `http://your-server-ip/`, you don't need it.

---

## What was changed from your original

- `trainer_simplified.js` now auto-detects the WebSocket address from the page
  URL. Locally it still uses `ws://127.0.0.1:8765`; when hosted it connects to
  your server's host automatically. Nothing else was modified.
