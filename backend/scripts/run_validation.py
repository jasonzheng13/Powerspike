"""Score the detector on validation/known_events.yaml.   python scripts/run_validation.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powerspike import validate  # noqa: E402

res = validate.run()
print(res.to_string(index=False))
print(validate.summary(res))
