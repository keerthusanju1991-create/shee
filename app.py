"""Vercel ASGI entry point. Secrets and academic data are supplied separately."""
import os

os.environ['DSU_RUNTIME'] = 'vercel'
from backend.api import app

__all__ = ['app']
