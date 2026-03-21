"""
store.py - Manages ~/.docksmith/ state on disk.

Layout:
  ~/.docksmith/
    images/   - one JSON manifest file per image (name_tag.json)
    layers/   - content-addressed tar files named sha256:<hash>
    cache/    - cache index (JSON)
"""

import os
import json
import shutil
import sys


DOCKSMITH_DIR = os.path.expanduser("~/.docksmith")
IMAGES_DIR = os.path.join(DOCKSMITH_DIR, "images")
LAYERS_DIR = os.path.join(DOCKSMITH_DIR, "layers")
CACHE_DIR  = os.path.join(DOCKSMITH_DIR, "cache")
CACHE_FILE = os.path.join(CACHE_DIR, "index.json")


class Store:
    def __init__(self):
        self._init_dirs()

    def _init_dirs(self):
        for d in [IMAGES_DIR, LAYERS_DIR, CACHE_DIR]:
            os.makedirs(d, exist_ok=True)

    # ------------------------------------------------------------------ images

    def _image_path(self, name, tag):
        safe = f"{name}_{tag}.json".replace("/", "_")
        return os.path.join(IMAGES_DIR, safe)

    def get_image(self, name, tag):
        path = self._image_path(name, tag)
        if not os.path.isfile(path):
            return None
        with open(path, "r") as f:
            return json.load(f)

    def save_image(self, manifest):
        name = manifest["name"]
        tag  = manifest["tag"]
        path = self._image_path(name, tag)
        with open(path, "w") as f:
            json.dump(manifest, f, indent=2)

    def list_images(self):
        images = []
        for fname in sorted(os.listdir(IMAGES_DIR)):
            if fname.endswith(".json"):
                fpath = os.path.join(IMAGES_DIR, fname)
                with open(fpath, "r") as f:
                    images.append(json.load(f))
        return images

    def remove_image(self, name, tag):
        manifest = self.get_image(name, tag)
        if manifest is None:
            print(f"Error: image '{name}:{tag}' not found")
            sys.exit(1)

        # Remove all layer files belonging to this image
        removed_layers = []
        for layer in manifest.get("layers", []):
            digest = layer["digest"]
            layer_path = self.layer_path(digest)
            if os.path.isfile(layer_path):
                os.remove(layer_path)
                removed_layers.append(digest[:19])  # sha256: + 12 chars

        # Remove manifest
        os.remove(self._image_path(name, tag))

        print(f"Untagged: {name}:{tag}")
        for d in removed_layers:
            print(f"Deleted layer: {d}")
        print(f"Successfully removed '{name}:{tag}'")

    # ------------------------------------------------------------------ layers

    def layer_path(self, digest):
        # digest is "sha256:<hex>"
        filename = digest.replace("sha256:", "sha256_")
        return os.path.join(LAYERS_DIR, filename)

    def has_layer(self, digest):
        return os.path.isfile(self.layer_path(digest))

    def save_layer(self, digest, data: bytes):
        path = self.layer_path(digest)
        with open(path, "wb") as f:
            f.write(data)

    def load_layer(self, digest) -> bytes:
        path = self.layer_path(digest)
        with open(path, "rb") as f:
            return f.read()

    # ------------------------------------------------------------------ cache

    def _load_cache(self):
        if not os.path.isfile(CACHE_FILE):
            return {}
        with open(CACHE_FILE, "r") as f:
            return json.load(f)

    def _save_cache(self, index):
        with open(CACHE_FILE, "w") as f:
            json.dump(index, f, indent=2)

    def cache_get(self, key):
        """Return layer digest if cache hit, else None."""
        index = self._load_cache()
        return index.get(key)

    def cache_set(self, key, digest):
        index = self._load_cache()
        index[key] = digest
        self._save_cache(index)
