"""Small monotone calibrators; fitted only on predictions for unseen patients."""

from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, softmax

EPS = 1e-7


@dataclass
class ScoreCalibrator:
    method: str = "identity"
    slope: float = 1.
    intercept: float = 0.

    def transform(self, probabilities):
        p = np.asarray(probabilities, dtype=float)
        if self.method == "identity":
            return p.copy()
        if self.method == "temperature":
            return softmax(np.log(np.clip(p, EPS, 1)) * self.slope, axis=1)
        if self.method != "sigmoid_logit" or p.shape[1] != 2:
            raise ValueError("Unknown calibration method or incompatible labels")
        q = np.clip(p[:, 1], EPS, 1-EPS)
        positive = expit(self.slope * (np.log(q)-np.log1p(-q)) + self.intercept)
        return np.column_stack([1-positive, positive])

    @classmethod
    def fit(cls, method, p, y, labels, weights=None):
        if method == "identity":
            return cls()
        p = np.asarray(p, dtype=float)
        indices = np.array([labels.index(v) for v in y])
        weights = np.ones(len(y)) if weights is None else np.asarray(weights, dtype=float)
        if len(p) != len(y) or not np.isfinite(p).all() or not np.isfinite(weights).all() or (weights <= 0).any():
            raise ValueError("Invalid calibration observations")
        if set(indices) != set(range(len(labels))):
            raise ValueError("Every class must be observed for calibration")
        if method not in {"temperature", "sigmoid_logit"} or (method == "sigmoid_logit" and len(labels) != 2):
            raise ValueError("Invalid calibration method")
        def loss(theta):
            model = cls(method, float(np.exp(theta[0])), float(theta[1]) if len(theta) == 2 else 0.)
            q = model.transform(p)
            likelihood = np.average(-np.log(np.clip(q[np.arange(len(y)), indices], EPS, 1)), weights=weights)
            # Shrink towards identity; constrain slope positive to preserve ordering.
            return likelihood + .005 * float(np.sum(np.square(theta)))
        n = 2 if method == "sigmoid_logit" else 1
        solution = minimize(loss, np.zeros(n), method="L-BFGS-B", bounds=[(-2.3, 2.3)] + ([(-8, 8)] if n == 2 else []))
        if not solution.success or not np.isfinite(solution.fun):
            raise ValueError("Calibration optimisation failed")
        return cls(method, float(np.exp(solution.x[0])), float(solution.x[1]) if n == 2 else 0.)


def calibrate_scores(calibrator, probabilities, frame, target):
    """Respect the existing deterministic no-anemia constraint after calibration."""
    p = calibrator.transform(probabilities)
    if target == "inflammation_anemia":
        from .core import anemia_status
        normal = anemia_status(frame).eq(0).to_numpy()
        p[normal] = [1., 0.]
    return p


def probability_metrics(y, p, labels):
    from sklearn.metrics import f1_score
    index = np.array([labels.index(v) for v in y])
    one_hot = np.eye(len(labels))[index]
    # Binary Brier uses the positive probability; multiclass uses sum of squared errors.
    brier = np.mean(np.square(p[:, 1]-one_hot[:, 1])) if len(labels) == 2 else np.mean(np.square(p-one_hot).sum(axis=1))
    bins = reliability_bins(y, p, labels)
    predicted = np.array(labels)[(p[:, 1] >= .5).astype(int) if len(labels) == 2 else p.argmax(axis=1)]
    return {"n": len(y), "positives": int(np.sum(index == 1)) if len(labels) == 2 else None,
            "log_loss": float(np.mean(-np.log(np.clip(p[np.arange(len(y)), index], EPS, 1)))),
            "brier": float(brier), "ece_10": float(sum(b["n"] * abs(b["mean_score"]-b["observed"]) for b in bins)/len(y)),
            "f1": float(f1_score(y, predicted, average="binary" if len(labels) == 2 else "macro", zero_division=0))}


def reliability_bins(y, p, labels):
    indices = np.array([labels.index(v) for v in y])
    scores = p[:, 1] if len(labels) == 2 else p.max(axis=1)
    outcome = indices == 1 if len(labels) == 2 else indices == p.argmax(axis=1)
    bin_id = np.minimum((scores*10).astype(int), 9)
    return [{"bin": b, "n": int(np.sum(bin_id == b)), "mean_score": float(scores[bin_id == b].mean()),
             "observed": float(outcome[bin_id == b].mean())} for b in range(10) if np.any(bin_id == b)]
