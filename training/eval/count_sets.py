"""Print utterance counts and hours of every registered eval set."""
import pyarrow.parquet as pq

from training.eval import sets

for name, s in sets.SETS.items():
    try:
        files = s.files()
    except FileNotFoundError as exc:
        print(f"{name:16s} missing ({exc})")
        continue
    rows = sum(pq.ParquetFile(p).metadata.num_rows for p in files)
    print(f"{name:16s} {s.split:5s} rows={rows:6d} files={len(files)}")
