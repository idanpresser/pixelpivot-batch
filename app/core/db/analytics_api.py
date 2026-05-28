"""Public API re-exports from database repositories.

Aggregates common repository functions for convenient imports across the
application. Simplifies access to conversions, images, metrics, priors,
pipeline runs, and analytics data.
"""

from .repositories.images import register_image, get_image_by_id
from .repositories.conversions import (
    insert_conversion,
    insert_conversions_batch,
    get_existing_conversion,
    is_benchmarked,
    remove_failed_images,
    get_conversion_count
)
from .repositories.metrics import (
    update_single_metric,
    update_lcp_metric,
    get_pending_metric_tasks,
    get_conversion_metrics
)
from .repositories.priors import get_quality_prior, update_quality_prior
from .repositories.pipeline import (
    create_pipeline_run,
    update_pipeline_run_phase,
    complete_pipeline_run,
    get_interrupted_run,
    get_pipeline_run_history
)
from .repositories.telemetry import insert_telemetry, insert_telemetry_batch
from .analytics import (
    get_dashboard_dataframe,
    get_benchmark_dataframe,
    get_quality_priors_dataframe
)
