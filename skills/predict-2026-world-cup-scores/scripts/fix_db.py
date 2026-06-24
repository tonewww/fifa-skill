import sqlite3
from common import MODEL_VERSION, now_utc
from predict_match import DEFAULT_PARAMETERS

conn = sqlite3.connect("data/worldcup2026.sqlite")
row = conn.execute("SELECT * FROM model_parameters ORDER BY as_of_date DESC LIMIT 1").fetchone()

# Make a copy of DEFAULT_PARAMETERS
params = DEFAULT_PARAMETERS.copy()

# Override specific params
params["wdl_score_calibration_weight"] = 0.25  # drastically lower the draw pull
params["zero_inflation"] = 0.15 # Use 15% ZIP
params["dixon_coles_rho"] = 0.0

columns = ["parameter_id", "model_version", "as_of_date", *params.keys(), "notes"]
placeholders = ", ".join("?" for _ in columns)
conn.execute(
    f"INSERT INTO model_parameters ({', '.join(columns)}) VALUES ({placeholders})",
    [
        "manual_zip_fix",
        MODEL_VERSION,
        now_utc(),
        *[params[key] for key in params.keys()],
        "Manual override to set ZIP to 0.15 and lower calibration weight to 0.25",
    ],
)
conn.commit()
conn.close()
print("Updated database!")
