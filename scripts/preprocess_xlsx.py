from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pandas as pd


DATA_DIR = Path("data")
SAVE_DIR = DATA_DIR / "processed"
SAVE_DIR.mkdir(parents=True, exist_ok=True)

SPAC_KEYWORD = "스팩"

FILES = {
    "ohl": "ohl.xlsx",
    "price": "주가.xlsx",
    "volume": "거래량.xlsx",
    "institution": "기관.xlsx",
    "free_float": "유동주식수.xlsx",
    "earnings": "earnings.xlsx",
    "shares_mcap": "상장주식수와시총.xlsx",
    "index": "index.xlsx",
}


def read_meta(path: Path) -> pd.DataFrame:
    meta = pd.read_excel(path, header=None, nrows=6)
    info = pd.DataFrame(
        {
            "col": meta.columns[1:],
            "ticker": meta.iloc[0, 1:].astype(str).values,
            "name": meta.iloc[1, 1:].astype(str).values,
            "item": meta.iloc[4, 1:].astype(str).values,
        }
    )
    info["code"] = info["ticker"].str.replace("^A", "", regex=True)
    return info


def find_spac_tickers() -> set[str]:
    spac_tickers: set[str] = set()

    for key, filename in FILES.items():
        if key == "index":
            continue

        path = DATA_DIR / filename
        if not path.exists():
            print(f"[WARN] missing file: {path}")
            continue

        info = read_meta(path)
        found = info.loc[info["name"].str.contains(SPAC_KEYWORD, na=False), "ticker"]
        spac_tickers.update(found.astype(str))

    return spac_tickers


def build_stock_metadata(spac_tickers: set[str]) -> pd.DataFrame:
    output_path = SAVE_DIR / "stock_metadata.parquet"
    if output_path.exists():
        metadata = pd.read_parquet(output_path)
        print(f"stock_metadata: ({len(metadata)}, 3) skipped")
        return metadata

    rows = []
    seen = set()

    for key, filename in FILES.items():
        if key == "index":
            continue

        path = DATA_DIR / filename
        if not path.exists():
            continue

        info = read_meta(path)
        info = info.loc[~info["ticker"].isin(spac_tickers), ["ticker", "code", "name"]]
        for row in info.drop_duplicates("ticker").itertuples(index=False):
            if row.ticker in seen:
                continue
            seen.add(row.ticker)
            rows.append({"ticker": row.ticker, "code": row.code, "name": row.name})

    metadata = pd.DataFrame(rows).sort_values("ticker").reset_index(drop=True)
    metadata.to_parquet(output_path, index=False)
    return metadata


def load_stock_excel(filename: str) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame]:
    path = DATA_DIR / filename
    df = pd.read_excel(path, header=None)

    meta = df.iloc[:6].copy()
    data = df.iloc[6:].copy()
    dates = pd.to_datetime(data.iloc[:, 0], errors="coerce")

    info = pd.DataFrame(
        {
            "col": meta.columns[1:],
            "ticker": meta.iloc[0, 1:].astype(str).values,
            "name": meta.iloc[1, 1:].astype(str).values,
            "item": meta.iloc[4, 1:].astype(str).values,
        }
    )
    info["code"] = info["ticker"].str.replace("^A", "", regex=True)
    return data, dates, info


def save_wide(
    data: pd.DataFrame,
    dates: pd.Series,
    info: pd.DataFrame,
    cols: list[int],
    save_name: str,
) -> dict[str, object]:
    output_path = SAVE_DIR / f"{save_name}.parquet"
    if output_path.exists():
        selected = pd.read_parquet(output_path)
        summary = {
            "name": save_name,
            "path": str(output_path).replace("\\", "/"),
            "rows": int(selected.shape[0]),
            "columns": int(selected.shape[1]),
            "start": pd.to_datetime(selected.index).min().strftime("%Y-%m-%d"),
            "end": pd.to_datetime(selected.index).max().strftime("%Y-%m-%d"),
            "non_null": int(selected.notna().sum().sum()),
            "skipped": True,
        }
        print(f"{save_name}: {selected.shape} {summary['start']}..{summary['end']} skipped")
        return summary

    selected = data.loc[:, cols].copy()
    tickers = info.set_index("col").loc[cols, "ticker"].astype(str).values

    selected.index = dates
    selected.columns = tickers
    selected.index.name = "date"

    selected = selected.loc[selected.index.notna()]
    selected = selected.sort_index()
    selected = selected.loc[~selected.index.duplicated(keep="last")]
    selected = selected.apply(pd.to_numeric, errors="coerce")

    selected.to_parquet(output_path)

    summary = {
        "name": save_name,
        "path": str(output_path).replace("\\", "/"),
        "rows": int(selected.shape[0]),
        "columns": int(selected.shape[1]),
        "start": selected.index.min().strftime("%Y-%m-%d"),
        "end": selected.index.max().strftime("%Y-%m-%d"),
        "non_null": int(selected.notna().sum().sum()),
    }
    print(f"{save_name}: {selected.shape} {summary['start']}..{summary['end']}")
    return summary


def process_single_file(filename: str, save_name: str, spac_tickers: set[str]) -> dict[str, object]:
    if (SAVE_DIR / f"{save_name}.parquet").exists():
        return save_wide(
            data=pd.DataFrame(),
            dates=pd.Series(dtype="datetime64[ns]"),
            info=pd.DataFrame(),
            cols=[],
            save_name=save_name,
        )

    data, dates, info = load_stock_excel(filename)
    info = info.loc[~info["ticker"].isin(spac_tickers)].copy()
    cols = info["col"].tolist()
    return save_wide(data=data, dates=dates, info=info, cols=cols, save_name=save_name)


def process_ohl(spac_tickers: set[str]) -> list[dict[str, object]]:
    save_names = ["stock_open", "stock_high", "stock_low"]
    if all((SAVE_DIR / f"{name}.parquet").exists() for name in save_names):
        return [
            save_wide(
                data=pd.DataFrame(),
                dates=pd.Series(dtype="datetime64[ns]"),
                info=pd.DataFrame(),
                cols=[],
                save_name=name,
            )
            for name in save_names
        ]

    data, dates, info = load_stock_excel(FILES["ohl"])
    info = info.loc[~info["ticker"].isin(spac_tickers)].copy()

    item_map = {
        "시가(원)": "stock_open",
        "고가(원)": "stock_high",
        "저가(원)": "stock_low",
    }

    summaries = []
    for item_name, save_name in item_map.items():
        cols = info.loc[info["item"] == item_name, "col"].tolist()
        summaries.append(save_wide(data=data, dates=dates, info=info, cols=cols, save_name=save_name))
    return summaries


def process_shares_mcap(spac_tickers: set[str]) -> list[dict[str, object]]:
    save_names = ["stock_listed_shares", "stock_market_cap"]
    if all((SAVE_DIR / f"{name}.parquet").exists() for name in save_names):
        return [
            save_wide(
                data=pd.DataFrame(),
                dates=pd.Series(dtype="datetime64[ns]"),
                info=pd.DataFrame(),
                cols=[],
                save_name=name,
            )
            for name in save_names
        ]

    data, dates, info = load_stock_excel(FILES["shares_mcap"])
    info = info.loc[~info["ticker"].isin(spac_tickers)].copy()

    item_map = {
        "상장주식수(주)": "stock_listed_shares",
        "시가총액(백만원)": "stock_market_cap",
    }

    summaries = []
    for item_name, save_name in item_map.items():
        cols = info.loc[info["item"] == item_name, "col"].tolist()
        summaries.append(save_wide(data=data, dates=dates, info=info, cols=cols, save_name=save_name))
    return summaries


def process_index() -> dict[str, object]:
    output_path = SAVE_DIR / "index.parquet"
    if output_path.exists():
        index_df = pd.read_parquet(output_path)
        summary = {
            "name": "index",
            "path": str(output_path).replace("\\", "/"),
            "rows": int(index_df.shape[0]),
            "columns": int(index_df.shape[1]),
            "start": pd.to_datetime(index_df.index).min().strftime("%Y-%m-%d"),
            "end": pd.to_datetime(index_df.index).max().strftime("%Y-%m-%d"),
            "non_null": int(index_df.notna().sum().sum()),
            "skipped": True,
        }
        print(f"index: {index_df.shape} {summary['start']}..{summary['end']} skipped")
        return summary

    path = DATA_DIR / FILES["index"]
    df = pd.read_excel(path, header=None)

    data = df.iloc[6:].copy()
    dates = pd.to_datetime(data.iloc[:, 0], errors="coerce")
    index_df = data.iloc[:, 1:].copy()
    index_df.index = dates
    index_df.index.name = "date"
    index_df.columns = ["kospi_open", "kospi_close", "kosdaq_open", "kosdaq_close"]

    index_df = index_df.loc[index_df.index.notna()]
    index_df = index_df.sort_index()
    index_df = index_df.loc[~index_df.index.duplicated(keep="last")]
    index_df = index_df.apply(pd.to_numeric, errors="coerce")

    index_df.to_parquet(output_path)

    summary = {
        "name": "index",
        "path": str(output_path).replace("\\", "/"),
        "rows": int(index_df.shape[0]),
        "columns": int(index_df.shape[1]),
        "start": index_df.index.min().strftime("%Y-%m-%d"),
        "end": index_df.index.max().strftime("%Y-%m-%d"),
        "non_null": int(index_df.notna().sum().sum()),
    }
    print(f"index: {index_df.shape} {summary['start']}..{summary['end']}")
    return summary


def main() -> None:
    missing = [filename for filename in FILES.values() if not (DATA_DIR / filename).exists()]
    if missing:
        raise FileNotFoundError(f"Missing required XLSX files: {missing}")

    spac_tickers = find_spac_tickers()
    metadata = build_stock_metadata(spac_tickers)

    print(f"Excluded SPAC tickers: {len(spac_tickers)}")
    print(f"Stock metadata rows: {len(metadata)}")

    summaries: list[dict[str, object]] = []
    summaries.extend(process_ohl(spac_tickers))
    summaries.append(process_single_file(FILES["price"], "stock_close", spac_tickers))
    summaries.append(process_single_file(FILES["volume"], "stock_volume", spac_tickers))
    summaries.append(process_single_file(FILES["institution"], "stock_institution", spac_tickers))
    summaries.append(process_single_file(FILES["free_float"], "stock_free_float", spac_tickers))
    summaries.append(process_single_file(FILES["earnings"], "stock_earnings_quarterly", spac_tickers))
    summaries.extend(process_shares_mcap(spac_tickers))
    summaries.append(process_index())

    summary = {
        "save_dir": str(SAVE_DIR).replace("\\", "/"),
        "excluded_spac_tickers": sorted(spac_tickers),
        "stock_count": int(len(metadata)),
        "outputs": summaries,
    }
    by_name = defaultdict(dict)
    for item in summaries:
        by_name[item["name"]] = item
    summary["outputs_by_name"] = dict(by_name)

    with (SAVE_DIR / "preprocess_summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("Preprocessing complete")


if __name__ == "__main__":
    main()
