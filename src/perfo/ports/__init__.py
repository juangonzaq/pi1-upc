"""Ports (hexagonal architecture). Adapters implement these contracts."""

from perfo.ports.ingestion import DocumentIngestionPort
from perfo.ports.masters import MasterDataPort
from perfo.ports.queue import QueuePort
from perfo.ports.recognition import RecognitionOutput, RecognitionPort, RecognizerUnavailable
from perfo.ports.registry import ModelRegistryPort, ModelVersionInfo
from perfo.ports.repository import RepositoryPort
from perfo.ports.storage import ObjectStoragePort
from perfo.ports.telemetry import TelemetryPort

__all__ = [
    "DocumentIngestionPort",
    "MasterDataPort",
    "ModelRegistryPort",
    "ModelVersionInfo",
    "ObjectStoragePort",
    "QueuePort",
    "RecognitionOutput",
    "RecognitionPort",
    "RecognizerUnavailable",
    "RepositoryPort",
    "TelemetryPort",
]
