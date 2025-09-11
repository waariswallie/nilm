from fastapi import FastAPI
from .api import router

app = FastAPI(title="NILM Pipeline")
app.include_router(router)
