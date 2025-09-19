from fastapi import FastAPI
from fastapi.responses import RedirectResponse
import os
import logging
from .config import settings
import os
from .api import router
from .site import site

app = FastAPI(title="NILM Pipeline")
app = FastAPI(title="NILM Service", version="0.1.0")


@app.on_event("startup")
def _tune_process():
	# Apply nice level if requested
	if settings.nice_level is not None and hasattr(os, "nice"):
		try:
			current = getattr(os, "nice")(0)
			target = settings.nice_level
			if target != current:
				new = getattr(os, "nice")(target - current)  # relative diff
				logging.info("Adjusted nice level from %s to %s", current, new)
		except Exception as e:  # noqa: BLE001
			logging.warning("Could not set nice level (%s): %s", settings.nice_level, e)
	# Apply CPU affinity
	if settings.cpu_affinity and hasattr(os, "sched_setaffinity"):
		try:
			cores = {int(c.strip()) for c in settings.cpu_affinity.split(',') if c.strip()}
			if cores:
				getattr(os, "sched_setaffinity")(0, cores)  # Linux only
				logging.info("Set CPU affinity to %s", cores)
		except Exception as e:  # noqa: BLE001
			logging.warning("Could not set CPU affinity (%s): %s", settings.cpu_affinity, e)
app.include_router(router)
app.include_router(site)


@app.get("/", tags=["meta"], summary="Service root")
def root(index: bool = False):
	"""Root endpoint.

	By default returns service metadata. If `?index=1` is supplied it redirects to `/docs`.
	This prevents a confusing 404 when a user opens the base URL in a browser.
	"""
	if index:
		return RedirectResponse(url="/docs")
	return {
		"service": "nilm-pipeline",
		"description": "Event-based NILM pipeline (minute data, step events, clustering)",
		"endpoints": ["/health", "/scan", "/docs"],
		"docs": "/docs",
		"git_sha": os.getenv("APP_GIT_SHA", "unknown"),
	}
