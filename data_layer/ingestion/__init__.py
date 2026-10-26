"""Data layer ingestion package — Dukascopy parquet market data integration."""

from data_layer.ingestion.dukascopy_fetcher import DukascopyFetcher
from data_layer.ingestion.histdata_fetcher import HistDataFetcher
from data_layer.ingestion.pipeline import IngestionPipeline

__all__ = ["DukascopyFetcher", "HistDataFetcher", "IngestionPipeline"]
