# -*- coding: utf-8 -*-
"""
/eval-dashboard — renders the results of the assistant evaluation runs.

The page shows ONLY what is in evals/results.json (written by evals/run_evals.py).
If the file is missing it says so — this dashboard never invents numbers.
"""

import json
import os

from flask import Blueprint, render_template

dash = Blueprint("dash", __name__)


def _results_path():
    return (os.environ.get("SS_EVAL_RESULTS")
            or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "evals", "results.json"))


@dash.get("/eval-dashboard")
def eval_dashboard():
    path = _results_path()
    data = None
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            data = None
    if not data:
        return render_template("eval_dashboard.html", data=None,
                               results_path=path), 200
    return render_template("eval_dashboard.html", data=data, results_path=path)
