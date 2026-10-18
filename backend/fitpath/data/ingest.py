"""Convert the raw H&M Kaggle files into compact Parquet tables.

Inputs (data/raw/): articles.csv[.zip], customers.csv[.zip], transactions_train.csv[.zip]
Outputs (data/parquet/):
  articles      one row per article, article_id as int
  customers     one row per customer, with a compact int key `cid`
  transactions  t_dat, cid, article_id, price, channel  (~31M rows)

The zipped CSVs are streamed, never unpacked to disk.

Usage: python -m fitpath.data.ingest   (from backend/)
"""
import time
import zipfile
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as papq

from fitpath.config import RAW, PARQUET, pq


def _open_csv(name: str):
    """Return a readable binary stream for data/raw/<name>.csv or its .zip."""
    plain = RAW / f"{name}.csv"
    if plain.exists():
        return open(plain, "rb")
    zpath = RAW / f"{name}.csv.zip"
    if not zpath.exists():
        raise FileNotFoundError(f"missing {plain} or {zpath}")
    zf = zipfile.ZipFile(zpath)
    member = next(n for n in zf.namelist() if n.endswith(".csv"))
    return zf.open(member)


def _stream_to_parquet(name: str, out: Path, types: dict) -> int:
    """Stream a CSV into a Parquet file in record batches; returns the row count."""
    rows = 0
    with _open_csv(name) as f:
        reader = pacsv.open_csv(
            f,
            read_options=pacsv.ReadOptions(block_size=64 << 20),
            convert_options=pacsv.ConvertOptions(column_types=types),
        )
        writer = None
        for batch in reader:
            if writer is None:
                writer = papq.ParquetWriter(out, batch.schema, compression="zstd")
            writer.write_batch(batch)
            rows += batch.num_rows
        if writer:
            writer.close()
    return rows


def main():
    t0 = time.time()
    tmp_c, tmp_t = PARQUET / "_customers_raw.parquet", PARQUET / "_transactions_raw.parquet"

    print("articles ...")
    n = _stream_to_parquet("articles", PARQUET / "_articles_raw.parquet", {"article_id": pa.string()})
    print(f"  {n:,} rows")

    print("customers ...")
    n = _stream_to_parquet("customers", tmp_c, {"customer_id": pa.string(), "age": pa.float32(), "postal_code": pa.string()})
    print(f"  {n:,} rows")

    print("transactions (streaming the zip, a few minutes) ...")
    n = _stream_to_parquet(
        "transactions_train", tmp_t,
        {"t_dat": pa.date32(), "customer_id": pa.string(), "article_id": pa.string(), "price": pa.float64(), "sales_channel_id": pa.int8()},
    )
    print(f"  {n:,} rows")

    con = duckdb.connect()
    con.execute("SET preserve_insertion_order = false")
    print("compacting ...")
    con.execute(f"""
        COPY (
          SELECT CAST(article_id AS BIGINT) AS article_id, * EXCLUDE (article_id)
          FROM read_parquet('{(PARQUET / "_articles_raw.parquet").as_posix()}')
        ) TO '{pq("articles")}' (FORMAT parquet, COMPRESSION zstd)
    """)
    con.execute(f"""
        CREATE TEMP TABLE cust AS
        SELECT row_number() OVER (ORDER BY customer_id)::INTEGER AS cid, *
        FROM read_parquet('{tmp_c.as_posix()}')
    """)
    con.execute(f"COPY cust TO '{pq('customers')}' (FORMAT parquet, COMPRESSION zstd)")
    con.execute(f"""
        COPY (
          SELECT t.t_dat, c.cid, CAST(t.article_id AS BIGINT) AS article_id, t.price, t.sales_channel_id AS channel
          FROM read_parquet('{tmp_t.as_posix()}') t
          JOIN cust c USING (customer_id)
          ORDER BY t.t_dat
        ) TO '{pq("transactions")}' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 1000000)
    """)
    for p in (tmp_c, tmp_t, PARQUET / "_articles_raw.parquet"):
        p.unlink(missing_ok=True)

    for name in ("articles", "customers", "transactions"):
        cnt = con.execute(f"SELECT count(*) FROM '{pq(name)}'").fetchone()[0]
        size = (PARQUET / f"{name}.parquet").stat().st_size / 1e6
        print(f"  {name:13s} {cnt:>12,} rows  {size:8.1f} MB")
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
