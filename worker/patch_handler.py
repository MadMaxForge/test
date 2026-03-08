"""
Patch handler.py to support video outputs from VHS_VideoCombine.

The default RunPod ComfyUI worker handler only collects 'images' from
ComfyUI workflow outputs. VHS_VideoCombine (used for video generation)
returns results under the 'gifs' key instead. This patch adds support
for collecting video files alongside images.
"""
import sys

HANDLER_PATH = '/handler.py'

with open(HANDLER_PATH, 'r') as f:
    content = f.read()

# The original function only checks for 'images' key
old_line = "        if 'images' in value and isinstance(value['images'], list):"
new_lines = (
    "        if 'images' in value and isinstance(value['images'], list):\n"
    "            images.append(value['images'][0])\n"
    "        elif 'gifs' in value and isinstance(value['gifs'], list):"
)

if old_line in content:
    # We need to be careful: the old code after the if is:
    #     images.append(value['images'][0])
    # We replace the if-line AND the next append line with our new block
    old_block = (
        "        if 'images' in value and isinstance(value['images'], list):\n"
        "            images.append(value['images'][0])"
    )
    new_block = (
        "        if 'images' in value and isinstance(value['images'], list):\n"
        "            images.append(value['images'][0])\n"
        "        elif 'gifs' in value and isinstance(value['gifs'], list):\n"
        "            images.append(value['gifs'][0])"
    )
    content = content.replace(old_block, new_block)
    with open(HANDLER_PATH, 'w') as f:
        f.write(content)
    print('handler.py patched: added video/gifs output support for VHS_VideoCombine')
else:
    print('WARNING: Could not find target line in handler.py', file=sys.stderr)
    print('handler.py content around get_output_images:', file=sys.stderr)
    # Show context for debugging
    for i, line in enumerate(content.split('\n')):
        if 'get_output_images' in line or 'images' in line.lower():
            print(f'  L{i+1}: {line}', file=sys.stderr)
    sys.exit(1)
