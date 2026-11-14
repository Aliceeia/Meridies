# Matterport hackathon quickstart

Local implementation of the [official quickstart](https://beta-app.matterport.com/2026-princeton-hackathon/quickstart), with its Python script, Jupyter notebook, 14,944-model index, and basemap.

## Run on Windows

The environment is already installed in `.venv`. From this directory:

```powershell
.\.venv\Scripts\python.exe run_quickstart.py
```

Open `mp_out/index.html` to see the section results and generated pictures. `mp_out/run_report.json` holds machine-readable results. The runner removes configured secrets and URL query strings from captured logs. Keep the results local: the CoStar join may contain licensed listing data.

Run selected sections (setup always runs):

```powershell
.\.venv\Scripts\python.exe run_quickstart.py --sections 2,6,7
```

Open the notebook interactively:

```powershell
.\.venv\Scripts\python.exe -m jupyterlab matterport_quickstart.ipynb
```

The `.py` is the master source and can also be run directly. Edit `MODEL` there to test another property; the full workflow uses the example `zvKHR9DHNjJ`, which has aerial data and MatterPak. Cells are marked with `# %%` for notebook-compatible editors. Run `.\.venv\Scripts\python.exe sync_notebook.py` after editing it to regenerate the notebook. The local version fixes an upstream download issue: when a resource server ignores a prefix Range request, it stops reading at the requested limit instead of downloading the entire file.

## Credentials

Existing credentials are read from `.env` (ignored by Git). Required names:

```dotenv
MATTERPORT_API_KEY=
MATTERPORT_API_TOKEN=
VEXCEL_API_KEY=
COSTAR_ZIP_PASSWORD=
COSTAR_KIT_URL=
# Or use COSTAR_KIT_ZIP for a local kit ZIP.
```

Matterport uses HTTP Basic authentication against production: key as username, token as password. Refresh rejected credentials using `/costar-keys` in the event Discord. When Matterport rejects authentication, the runner reports sections 3–5 and 8 as blocked and uses coordinates from the shipped CSV for independent aerial tests. This fallback does not validate access to Matterport's Model API.

## Sections

1. Credential check and shared helpers
2. Nearby properties and model map
3. Model metadata, preview, and tour URL
4. Panorama stitching and furnished/defurnished comparison
5. Room dimensions, wall graph, floor plans, DXF
6. Sampled Gaussian splat, sky cubemap, drone image, flight path
7. Vexcel ortho overlay and CoStar listing join
8. MatterPak archive inspection and sampled point-cloud renders

Section 9 of the source is project ideas and help, not executable code. The script samples 60 MiB of the splat and every tenth MatterPak point. A complete run normally transfers about 200 MB. Optional textured OBJ extraction is controlled by `GET_OBJ` in the master script. The static gallery displays rendered images; the official page provides interactive 3D viewers.

## Recreate the environment

```powershell
$env:UV_CACHE_DIR="$PWD\.uv-cache"
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
```

Source assets: `https://static.matterport.com/hackathon/26.15.35-main-35-g24e0220/assets/quickstart/`.
