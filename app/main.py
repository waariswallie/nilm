from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from .api import router

app = FastAPI(title="NILM Pipeline")
app.include_router(router)


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
	}
