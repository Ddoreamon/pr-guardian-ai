"""Single source of truth for the product version.

`pyproject.toml` reads this file (hatch dynamic version), the FastAPI app, the
/health payload, the page footer, the README and the CHANGELOG all quote it. The
version describes the running build, not what the README promises: a deployment
that runs older code keeps reporting the older version.
"""

__version__ = "1.0.0"
