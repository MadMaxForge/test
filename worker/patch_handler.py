"""
Patch handler.py to support video outputs from VHS_VideoCombine.

The default RunPod ComfyUI worker handler only collects 'images' from
ComfyUI workflow outputs. VHS_VideoCombine (used for video generation)
returns results under the 'gifs' key instead. This patch adds support
for collecting video files alongside images.

Strategy: Use multiple approaches to find and patch the correct location,
since handler.py varies across runpod/worker-comfyui versions.
"""
import sys

HANDLER_PATH = '/handler.py'

with open(HANDLER_PATH, 'r') as f:
    content = f.read()
    original = content

patched = False

# ---- Approach 1: v5.7.x handler ----
# The v5.7.x handler checks: if "images" in node_output:
# We add gifs->images normalization right before that check
if 'if "images" in node_output:' in content and '"gifs"' not in content:
    content = content.replace(
        'if "images" in node_output:',
        'if "gifs" in node_output and "images" not in node_output:\n'
        '                        node_output["images"] = node_output["gifs"]\n'
        '                    if "images" in node_output:',
        1
    )
    patched = True
    print('handler.py patched (v5.7.x): added gifs->images normalization')

# ---- Approach 2: older handler (get_output_images function) ----
if not patched:
    old_pattern = "if 'images' in value and isinstance(value['images'], list):"
    if old_pattern in content and "'gifs'" not in content:
        content = content.replace(
            "if 'images' in value and isinstance(value['images'], list):\n"
            "            images.append(value['images'][0])",
            "if 'images' in value and isinstance(value['images'], list):\n"
            "            images.append(value['images'][0])\n"
            "        elif 'gifs' in value and isinstance(value['gifs'], list):\n"
            "            images.append(value['gifs'][0])",
        )
        patched = True
        print('handler.py patched (legacy): added gifs to get_output_images')

if patched:
    with open(HANDLER_PATH, 'w') as f:
        f.write(content)
    print('Patch applied successfully.')
else:
    print('ERROR: Could not patch handler.py', file=sys.stderr)
    lines = original.split('\n')
    for i, line in enumerate(lines):
        if 'image' in line.lower() or 'gif' in line.lower():
            print(f'  L{i+1}: {line}', file=sys.stderr)
    sys.exit(1)
