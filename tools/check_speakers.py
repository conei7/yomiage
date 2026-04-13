import sys
sys.path.insert(0, ".")
from core.voice import VCHandler

vc = VCHandler(in_executer=False)

speakers = vc.get_speakers()
print(f"get_speakers() returned {len(speakers)} items")

layered = vc.get_layered_speakers_list(credit=True)
print(f"get_layered_speakers_list() returned {len(layered)} names")

for i, item in enumerate(layered):
    print(f"  [{i+1}] {item['name']} - styles: {item['styles'][:2]}{'...' if len(item['styles']) > 2 else ''}")

options_per_dropdown = 25
pages = (len(layered) + options_per_dropdown - 1) // options_per_dropdown
print(f"\nTotal pages in dropdown: {pages}")
