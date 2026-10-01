"""Print the schema and first rows of a parquet file (audio bytes elided)."""
import sys

import pyarrow.parquet as pq

for path in sys.argv[1:]:
    f = pq.ParquetFile(path)
    print("==", path)
    print(f.schema_arrow)
    print("rows:", f.metadata.num_rows)
    t = f.read_row_group(0).slice(0, 3).to_pylist()
    for row in t:
        out = {}
        for k, v in row.items():
            if isinstance(v, dict) and "bytes" in v:
                out[k] = {"bytes": len(v["bytes"] or b""), "path": v.get("path")}
            else:
                out[k] = v
        print(out)
