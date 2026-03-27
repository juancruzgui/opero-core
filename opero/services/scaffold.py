"""Project scaffolding — create React + FastAPI + Supabase structure.

Called during `opero init` to set up the standard project structure
so services are ready to start before any agent begins coding.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def scaffold_project(project_path: str) -> dict:
    """Create the standard React + FastAPI + Supabase project structure.

    Returns a dict with results for each service.
    """
    results = {}
    root = Path(project_path)

    results["frontend"] = _scaffold_frontend(root)
    results["backend"] = _scaffold_backend(root)
    results["database"] = _scaffold_database(root)

    return results


def _scaffold_frontend(root: Path) -> dict:
    """Scaffold React + Vite + Tailwind frontend."""
    if (root / "package.json").exists():
        return {"status": "exists", "message": "package.json already exists"}

    try:
        # Create Vite React app in the project root
        subprocess.run(
            ["npm", "create", "vite@latest", ".", "--", "--template", "react"],
            cwd=str(root),
            capture_output=True,
            timeout=60,
            input=b"y\n",  # accept overwrite prompt
        )

        # Install dependencies
        subprocess.run(
            ["npm", "install"],
            cwd=str(root),
            capture_output=True,
            timeout=120,
        )

        # Install Tailwind CSS
        subprocess.run(
            ["npm", "install", "-D", "tailwindcss", "@tailwindcss/vite"],
            cwd=str(root),
            capture_output=True,
            timeout=60,
        )

        # Configure Tailwind in vite.config.js
        vite_config = root / "vite.config.js"
        if vite_config.exists():
            vite_config.write_text("""import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
})
""")

        # Add Tailwind to CSS
        css_file = root / "src" / "index.css"
        if css_file.exists():
            css_file.write_text('@import "tailwindcss";\n')

        return {"status": "created", "message": "React + Vite + Tailwind"}
    except FileNotFoundError:
        return {"status": "error", "message": "npm not found — install Node.js"}
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "npm timed out"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _scaffold_backend(root: Path) -> dict:
    """Scaffold FastAPI backend."""
    backend_dir = root / "backend"

    if (backend_dir / "main.py").exists():
        return {"status": "exists", "message": "backend/main.py already exists"}

    try:
        backend_dir.mkdir(exist_ok=True)

        # main.py
        (backend_dir / "main.py").write_text("""from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}
""")

        # requirements.txt
        (backend_dir / "requirements.txt").write_text("""fastapi>=0.100.0
uvicorn[standard]>=0.20.0
supabase>=2.0.0
python-dotenv>=1.0.0
""")

        # Install deps in the venv if available
        venv_pip = root / ".opero" / "venv" / "bin" / "pip"
        if venv_pip.exists():
            subprocess.run(
                [str(venv_pip), "install", "-r", "requirements.txt", "-q"],
                cwd=str(backend_dir),
                capture_output=True,
                timeout=120,
            )

        return {"status": "created", "message": "FastAPI backend"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _scaffold_database(root: Path) -> dict:
    """Init Supabase local if available."""
    if (root / "supabase").exists():
        return {"status": "exists", "message": "supabase/ already exists"}

    try:
        result = subprocess.run(
            ["supabase", "init"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return {"status": "created", "message": "Supabase initialized"}
        else:
            return {"status": "error", "message": result.stderr[:200]}
    except FileNotFoundError:
        # Create a placeholder so the dashboard shows it as ready
        (root / "supabase").mkdir(exist_ok=True)
        (root / "supabase" / "config.toml").write_text("# Install Supabase CLI: npm install -g supabase\n# Then run: supabase init\n")
        return {"status": "placeholder", "message": "supabase/ created — install Supabase CLI to use"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
