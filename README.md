# Docksmith — Setup & Execution Guide

Docksmith is a lightweight containerization system inspired by Docker.  
It demonstrates how image building, caching, and process isolation work at a low level.

---

## 🔧 System Requirements

Make sure you are using a **Linux environment (Ubuntu recommended)**.

Install dependencies:

```bash
sudo apt update
sudo apt install -y python3 python3-pip util-linux


## Project Structure

```
docksmith/
├── docksmith.py          # Main CLI
├── builder.py            # Build engine
├── cache.py              # Cache key logic
├── runtime.py            # Container runtime + isolation
├── store.py              # Disk state management
├── tar_utils.py          # Reproducible tar creation
├── setup_base_image.py   # One-time base image import
└── sample_app/
    ├── Docksmithfile
    └── run.sh
```

## Step 0: Install + First-time Setup

```bash
# Make the CLI executable
chmod +x docksmith.py

# Import Alpine base image (downloads ~2.7MB, only needed once)
python3 setup_base_image.py
```

Or run the one-shot Linux bootstrap script:

```bash
chmod +x setup_linux.sh
./setup_linux.sh
```

# Incase of the SSL certificate issue run the following commands:
```bash
wget --no-check-certificate "https://dl-cdn.alpinelinux.org/alpine/v3.18/releases/x86_64/alpine-minirootfs-3.18.4-x86_64.tar.gz"
python3 setup_base_image.py --local alpine-minirootfs-3.18.4-x86_64.tar.gz
```

#Also to fix the SSL certificates properly
```bash
sudo apt update && sudo apt install -y ca-certificates
sudo update-ca-certificates
```

## COPY Pattern Support
# Direct file copy
COPY app.py /app/

# Wildcards
COPY src/*.py /app/

# Recursive patterns
COPY src/**/*.py /app/


## Build Behavior Notes

- `COPY` includes all matched files in cache hashing
- `RUN` creates delta layers (adds, edits, deletions)
- `ENV` variables are inherited and can be overridden
- Removing images also cleans related cache entries
## Step 1: Cold Build (all CACHE MISS)

```bash
python3 docksmith.py build -t myapp:latest ./sample_app
```

Expected output:
```
Step 1/7 : FROM alpine:3.18
Step 2/7 : WORKDIR /app
Step 3/7 : ENV APP_ENV=production
Step 4/7 : ENV GREETING=Hello
Step 5/7 : COPY . /app [CACHE MISS] 0.06s
Step 6/7 : RUN echo "Build complete. Files in /app:" && ls /app [CACHE MISS]Build complete. Files in /app:
Docksmithfile  run.sh
 0.11s
Step 7/7 : CMD ["sh", "run.sh"]

Successfully built sha256:14d23835e1a8 myapp:latest (0.18s)
```

## Step 2: Warm Build (all CACHE HIT)

```bash
python3 docksmith.py build -t myapp:latest ./sample_app
```

Expected: [CACHE HIT] for all layer steps


## Step 3: Partial Cache Invalidation

```bash
# Edit a source file
echo "# changed" >> sample_app/run.sh

# Rebuild — COPY and everything below it should be CACHE MISS
python3 docksmith.py build -t myapp:latest ./sample_app
```

Note: this also applies when files matched by `COPY` globs (`*`, `**`) are changed.

Expected: 
COPY → [CACHE MISS]
RUN  → [CACHE MISS]


## Step 4: List Images

```bash
python3 docksmith.py images
```

## Step 5: Run Container

```bash
python3 docksmith.py run myapp:latest
```
Application output displayed
Container exits successfully


## Step 6: Env Override

```bash
python3 docksmith.py run -e GREETING="Hi there" myapp:latest
```
GREETING value updated inside container
 
## Step 7: Verify Isolation

```bash
# After running container, check host — this file must NOT exist
ls /tmp/isolation_test.txt  # should say "No such file or directory"
```

## Step 8: Remove Image

```bash
python3 docksmith.py rmi myapp:latest
```

## Troubleshooting

**"unshare not found"**: `sudo apt install util-linux`

**"chroot: Operation not permitted"**: Make sure you're on Linux (not macOS).
The `--map-root-user` flag in unshare handles this without needing sudo.

**Build fails with "base image not found"**: Run `python3 setup_base_image.py` first.
