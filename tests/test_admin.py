"""Tests for admin functionality."""
import pytest
from pathlib import Path
from datetime import datetime
import tempfile
import os


def test_models_import():
    """Test that all models can be imported."""
    from code_hub.models import (
        Project, Module, ProjectFile, Keyword, ProjectKeyword,
        Dependency, ProjectVector, LOCHistory, ScanLog, ProjectFTS,
        create_tables, drop_tables
    )
    assert LOCHistory is not None
    assert ScanLog is not None


def test_scanner_functions_import():
    """Test that scanner functions can be imported."""
    from code_hub.scanner import (
        get_changed_projects,
        scan_changed_projects,
        record_loc_history
    )
    assert get_changed_projects is not None
    assert scan_changed_projects is not None
    assert record_loc_history is not None


def test_scheduler_import():
    """Test that scheduler can be imported."""
    from code_hub.scheduler import (
        start_scheduler,
        stop_scheduler,
        get_scheduler,
        get_next_scan_time,
        trigger_scan_now
    )
    assert start_scheduler is not None
    assert stop_scheduler is not None


def test_scheduler_start_stop():
    """Test scheduler can start and stop."""
    from code_hub.scheduler import start_scheduler, stop_scheduler, get_scheduler

    scheduler = start_scheduler(hour=7, minute=0)
    assert scheduler is not None
    assert scheduler.running

    stop_scheduler()
    sched = get_scheduler()
    assert sched is None


def test_loc_history_model():
    """Test LOCHistory model creation."""
    from code_hub.models import db, create_tables, LOCHistory, Project

    db.connect(reuse_if_open=True)
    create_tables()

    # Model should exist
    assert LOCHistory._meta.table_name == 'loc_history'


def test_scan_log_model():
    """Test ScanLog model creation."""
    from code_hub.models import db, create_tables, ScanLog

    db.connect(reuse_if_open=True)
    create_tables()

    # Model should exist
    assert ScanLog._meta.table_name == 'scan_logs'


def test_api_models():
    """Test that API response models can be instantiated."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / 'code_hub'))

    # Import the pydantic models from server
    from code_hub.server import (
        ChangedProjectResponse,
        ScanResultResponse,
        ScanLogResponse,
        LOCHistoryEntry,
        SchedulerStatusResponse
    )

    # Test instantiation
    changed = ChangedProjectResponse(
        name="test",
        path="/path/to/test",
        last_modified="2024-01-01T00:00:00",
        scanned_at=None,
        is_new=True
    )
    assert changed.name == "test"

    scan_result = ScanResultResponse(
        scan_type="incremental",
        projects_found=5,
        projects_scanned=3,
        errors=[],
        triggered_by="api"
    )
    assert scan_result.projects_found == 5

    loc_entry = LOCHistoryEntry(
        recorded_at="2024-01-01T00:00:00",
        lines_of_code=1000,
        file_count=50
    )
    assert loc_entry.lines_of_code == 1000

    status = SchedulerStatusResponse(
        running=True,
        next_scan="2024-01-02T07:00:00"
    )
    assert status.running == True
