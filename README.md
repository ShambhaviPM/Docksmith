# Docksmith — Setup & Usage Guide

## Prerequisites (Ubuntu)

```bash
sudo apt update
sudo apt install -y python3 python3-pip util-linux
```

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

`COPY` supports:
- plain paths (single file or directory), e.g. `COPY app.py /app/`
- single-level wildcards (`*`), e.g. `COPY src/*.py /app/`
- recursive wildcards (`**`), e.g. `COPY src/**/*.py /app/`

## Build Behavior Notes

- `COPY` cache hashing includes all matched files, including files discovered recursively through matched directories.
- `RUN` delta layers include adds, edits, and deletions (deletions are encoded internally as whiteouts).
- `ENV` values from the base image are inherited first, then overridden by `ENV` instructions in the current build.
- `docksmith rmi` also removes stale cache-index entries that point to deleted layer digests.

## Step 1: Cold Build (all CACHE MISS)

```bash
python3 docksmith.py build -t myapp:latest ./sample_app
```

Expected output:
```
Step 1/5 : FROM alpine:3.18
Step 2/5 : WORKDIR /app
Step 3/5 : ENV APP_ENV=production
Step 4/5 : COPY . /app [CACHE MISS] 0.09s
Step 5/5 : RUN echo "Build complete..." [CACHE MISS] 0.82s
Successfully built sha256:a3f9b2c1xxxx myapp:latest (1.23s)
```

## Step 2: Warm Build (all CACHE HIT)

```bash
python3 docksmith.py build -t myapp:latest ./sample_app
```

Expected: all layer steps show [CACHE HIT], completes near-instantly.

## Step 3: Partial Cache Invalidation

```bash
# Edit a source file
echo "# changed" >> sample_app/run.sh

# Rebuild — COPY and everything below it should be CACHE MISS
python3 docksmith.py build -t myapp:latest ./sample_app
```

Note: this also applies when files matched by `COPY` globs (`*`, `**`) are changed.

## Step 4: List Images

```bash
python3 docksmith.py images
```

## Step 5: Run Container

```bash
python3 docksmith.py run myapp:latest
```

## Step 6: Env Override

```bash
python3 docksmith.py run -e GREETING="Hi there" myapp:latest
```

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
