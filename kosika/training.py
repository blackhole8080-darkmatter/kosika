"""Training real weights inside Kosika, on Kosika's own verified material.

Kosika does not train a language model's weights: that needs an optimiser, a
GPU and a corpus far larger than one project. What it does need is a small
model of its own -- the one that decides which source passages a virtual
specialist should read before it answers. That model has weights, and until
now they were the hand-made lexical score in ``virtual_model.py``. This module
learns them instead, from material a person has already verified.

The training data is already in the profile: every ``verified_examples`` entry
quotes source evidence. A passage that contains a cited line is a real
positive for that task; a passage that contains none of its citations is a real
negative. Nothing is scraped from model output, so training cannot learn from
the model's own guesses.

Two limits are stated rather than hidden:

* This is a small hashed-feature network -- a few thousand weights, seconds on
  a laptop's CPU. It ranks passages; it does not write text.
* Training is deterministic. The same examples, sources, seed and
  hyperparameters produce a checkpoint with the same digest, so a run can be
  repeated and checked rather than trusted.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CHECKPOINT_SCHEMA = "kosika.ranker/v1"
MAX_CHECKPOINT_BYTES = 16_000_000
MAX_PAIRS = 4_000
#: Scalars are written into the last indices of every feature vector, so a
#: hashed token can never be mistaken for one of them.
_SCALARS = 4
_TOKEN = re.compile(r"[a-z0-9_]{2,}")
#: Adam's usual constants: a moving average of the gradient and of its square.
_ADAM_BETA1 = 0.9
_ADAM_BETA2 = 0.999
_ADAM_EPSILON = 1e-8


class TrainingError(ValueError):
    """A training set, a configuration or a checkpoint is not usable."""


@dataclass(frozen=True)
class TrainingConfig:
    """What one training run is allowed to spend, and how it behaves."""

    epochs: int = 30
    batch_size: int = 8
    learning_rate: float = 0.05
    hidden: int = 24
    feature_dim: int = 512
    negative_ratio: int = 3
    seed: int = 7

    def __post_init__(self) -> None:
        if type(self.epochs) is not int or not 1 <= self.epochs <= 500:
            raise TrainingError("epochs must be an integer from 1 to 500")
        if type(self.batch_size) is not int or not 1 <= self.batch_size <= 256:
            raise TrainingError("batch_size must be an integer from 1 to 256")
        if (
            not isinstance(self.learning_rate, float)
            or not 0.0 < self.learning_rate <= 1.0
        ):
            raise TrainingError("learning_rate must be greater than 0 and at most 1")
        if type(self.hidden) is not int or not 1 <= self.hidden <= 256:
            raise TrainingError("hidden must be an integer from 1 to 256")
        if type(self.feature_dim) is not int or not 64 <= self.feature_dim <= 4096:
            raise TrainingError("feature_dim must be an integer from 64 to 4096")
        if type(self.negative_ratio) is not int or not 1 <= self.negative_ratio <= 10:
            raise TrainingError("negative_ratio must be an integer from 1 to 10")
        if type(self.seed) is not int:
            raise TrainingError("seed must be an integer")

    def as_dict(self) -> dict[str, Any]:
        return {
            "epochs": self.epochs,
            "batch_size": self.batch_size,
            "learning_rate": self.learning_rate,
            "hidden": self.hidden,
            "feature_dim": self.feature_dim,
            "negative_ratio": self.negative_ratio,
            "seed": self.seed,
        }


@dataclass(frozen=True)
class TrainingPair:
    """One question and one passage, marked relevant or not.

    ``example`` is the index of the verified example the pair came from, so a
    held-out split can keep whole examples out of training rather than leaking
    one example's passages across both sides.
    """

    query: str
    text: str
    label: int
    source: str = ""
    example: int = 0


@dataclass(frozen=True)
class TrainingSet:
    """The examples read from a profile, and what they became."""

    name: str
    pairs: tuple[TrainingPair, ...]

    @property
    def positives(self) -> int:
        return sum(1 for pair in self.pairs if pair.label == 1)

    @property
    def negatives(self) -> int:
        return len(self.pairs) - self.positives


class _UnusedProvider:
    """The profile loader never asks a model anything; prove that it cannot."""

    def complete(self, request: Any) -> Any:
        raise AssertionError("reading training pairs must not call a model")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _bucket(name: str, size: int) -> int:
    """A stable bucket for a feature name.

    ``hash()`` is salted per process, so a checkpoint trained in one run would
    not score the same way in the next. A digest of the name is stable
    everywhere, which is what a saved weight matrix requires.
    """

    digest = hashlib.blake2b(name.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % size


def features(query: str, text: str, feature_dim: int) -> dict[int, float]:
    """The sparse input vector for one (question, passage) pair.

    Shared words and shared word pairs land in their own buckets, the passage's
    own vocabulary lands in others at a lower weight, and a few scalars say how
    much of the question the passage covers at all. Ordering is sorted rather
    than set-ordered so repeated runs add floats in the same order and produce
    bit-identical checkpoints.
    """

    if feature_dim <= _SCALARS:
        raise TrainingError("feature_dim must leave room for features and scalars")
    body = feature_dim - _SCALARS
    asked = _tokens(query)
    document = _tokens(text)
    if not asked or not document:
        return {}
    asked_set = set(asked)
    document_set = set(document)
    found: dict[int, float] = {}
    for token in sorted(asked_set & document_set):
        found[_bucket("shared:" + token, body)] = 1.0
    asked_pairs = set(zip(asked, asked[1:]))
    document_pairs = set(zip(document, document[1:]))
    for left, right in sorted(asked_pairs & document_pairs):
        key = _bucket(f"bigram:{left} {right}", body)
        found[key] = found.get(key, 0.0) + 1.0
    for token in sorted(document_set):
        key = _bucket("passage:" + token, body)
        found[key] = found.get(key, 0.0) + 0.25
    shared = len(asked_set & document_set)
    found[body] = shared / math.sqrt(len(asked_set) * len(document_set))
    found[body + 1] = shared / (shared + 1.0)
    found[body + 2] = min(len(document_set) / 25.0, 3.0) / 3.0
    found[body + 3] = 1.0 if shared else 0.0
    return found


def _sigmoid(value: float) -> float:
    clipped = max(-60.0, min(60.0, value))
    return 1.0 / (1.0 + math.exp(-clipped))


def _forward(
    w1: list[list[float]],
    b1: list[float],
    v: list[float],
    b2: float,
    sparse: dict[int, float],
) -> tuple[float, list[float]]:
    """The probability this passage is relevant, and the hidden layer."""

    hidden: list[float] = []
    for row, bias in zip(w1, b1):
        total = bias
        for index, value in sparse.items():
            total += value * row[index]
        hidden.append(total if total > 0.0 else 0.0)
    out = b2
    for weight, activation in zip(v, hidden):
        out += weight * activation
    return _sigmoid(out), hidden


def _evaluate(
    w1: list[list[float]],
    b1: list[float],
    v: list[float],
    b2: float,
    pairs: tuple[TrainingPair, ...],
    feature_dim: int,
) -> dict[str, float]:
    losses: list[float] = []
    right = 0
    positive_scores: list[float] = []
    negative_scores: list[float] = []
    for pair in pairs:
        probability, _ = _forward(
            w1, b1, v, b2, features(pair.query, pair.text, feature_dim)
        )
        bounded = min(max(probability, 1e-7), 1.0 - 1e-7)
        if pair.label == 1:
            losses.append(-math.log(bounded))
            positive_scores.append(probability)
        else:
            losses.append(-math.log(1.0 - bounded))
            negative_scores.append(probability)
        if (probability >= 0.5) == (pair.label == 1):
            right += 1
    return {
        "loss": sum(losses) / max(len(losses), 1),
        "accuracy": right / max(len(pairs), 1),
        "mean_positive": (
            sum(positive_scores) / len(positive_scores) if positive_scores else 0.0
        ),
        "mean_negative": (
            sum(negative_scores) / len(negative_scores) if negative_scores else 0.0
        ),
    }


class TrainedRanker:
    """A trained, saved, and reloadable relevance model."""

    def __init__(
        self,
        *,
        name: str,
        config: TrainingConfig,
        w1: list[list[float]],
        b1: list[float],
        v: list[float],
        b2: float,
        metrics: dict[str, float],
        created: str | None = None,
        history: list[float] | None = None,
    ) -> None:
        self.name = name
        self.config = config
        self.feature_dim = config.feature_dim
        self.hidden = config.hidden
        self.w1 = w1
        self.b1 = b1
        self.v = v
        self.b2 = b2
        self.metrics = dict(metrics)
        self.created = created or datetime.now(timezone.utc).isoformat()
        #: Loss after each epoch, so a run that went nowhere can be seen.
        self.history = list(history or [])

    # -- using it -----------------------------------------------------------

    def score(self, query: str, text: str) -> float:
        """How relevant this passage is to this question, from 0 to 1."""

        probability, _ = _forward(
            self.w1, self.b1, self.v, self.b2, features(query, text, self.feature_dim)
        )
        return probability

    # -- keeping it ---------------------------------------------------------

    def identity(self) -> str:
        """A digest of the weights and the configuration, not of the time.

        ``created`` and ``metrics`` are excluded, so retraining the same data
        with the same seed yields the same identity even on another day.
        """

        payload = {
            "schema": CHECKPOINT_SCHEMA,
            "name": self.name,
            "config": self.config.as_dict(),
            "weights": {
                "w1": self.w1,
                "b1": self.b1,
                "v": self.v,
                "b2": self.b2,
            },
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": CHECKPOINT_SCHEMA,
            "name": self.name,
            "created": self.created,
            "config": self.config.as_dict(),
            "feature_dim": self.feature_dim,
            "hidden": self.hidden,
            "weights": {
                "w1": self.w1,
                "b1": self.b1,
                "v": self.v,
                "b2": self.b2,
            },
            "metrics": self.metrics,
            "digest": self.identity(),
        }

    def summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "digest": self.identity(),
            "feature_dim": self.feature_dim,
            "hidden": self.hidden,
            "weights": self.hidden * self.feature_dim + 2 * self.hidden + 1,
            "metrics": dict(self.metrics),
            "config": self.config.as_dict(),
            "created": self.created,
        }

    def save(self, path: str | Path) -> Path:
        """Write the checkpoint atomically, so a stopped run leaves no half file."""

        target = Path(path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                json.dump(self.as_dict(), stream, ensure_ascii=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            temporary = None
        except OSError as exc:
            raise TrainingError(f"Could not save checkpoint: {exc}") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return target


def _strict_weights(payload: Any, hidden: int, feature_dim: int) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {"w1", "b1", "v", "b2"}:
        raise TrainingError("Checkpoint weights need exactly w1, b1, v, and b2")
    w1, b1, v, b2 = payload["w1"], payload["b1"], payload["v"], payload["b2"]
    if (
        not isinstance(w1, list)
        or len(w1) != hidden
        or any(
            not isinstance(row, list)
            or len(row) != feature_dim
            or any(type(value) is not float for value in row)
            for row in w1
        )
    ):
        raise TrainingError(
            f"Checkpoint w1 must be {hidden} rows of {feature_dim} numbers"
        )
    for name, vector in (("b1", b1), ("v", v)):
        if (
            not isinstance(vector, list)
            or len(vector) != hidden
            or any(type(value) is not float for value in vector)
        ):
            raise TrainingError(f"Checkpoint {name} must be {hidden} numbers")
    if type(b2) is not float:
        raise TrainingError("Checkpoint b2 must be one number")
    return {"w1": w1, "b1": b1, "v": v, "b2": b2}


def load_ranker(path: str | Path) -> TrainedRanker:
    """Read a checkpoint, refusing anything that is not exactly one.

    The digest is recomputed, so a weight that was edited by hand is reported
    as changed rather than quietly used.
    """

    source = Path(path)
    try:
        if source.stat().st_size > MAX_CHECKPOINT_BYTES:
            raise TrainingError(
                f"Checkpoint exceeds {MAX_CHECKPOINT_BYTES // 1_000_000} MB"
            )
        payload = json.loads(source.read_text(encoding="utf-8"))
    except TrainingError:
        raise
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise TrainingError(f"Cannot read checkpoint {source}: {exc}") from exc
    allowed = {
        "schema",
        "name",
        "created",
        "config",
        "feature_dim",
        "hidden",
        "weights",
        "metrics",
        "digest",
    }
    if not isinstance(payload, dict) or set(payload) != allowed:
        raise TrainingError("Checkpoint must have exactly: " + ", ".join(sorted(allowed)))
    if payload.get("schema") != CHECKPOINT_SCHEMA:
        raise TrainingError(f"Checkpoint must use schema {CHECKPOINT_SCHEMA}")
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 100:
        raise TrainingError("Checkpoint name must be 1..100 characters")
    raw_config = payload.get("config")
    if not isinstance(raw_config, dict):
        raise TrainingError("Checkpoint config must be an object")
    config_keys = {
        "epochs",
        "batch_size",
        "learning_rate",
        "hidden",
        "feature_dim",
        "negative_ratio",
        "seed",
    }
    if set(raw_config) != config_keys:
        raise TrainingError("Checkpoint config is incomplete")
    try:
        config = TrainingConfig(**raw_config)
    except TypeError as exc:
        raise TrainingError(f"Checkpoint config is not readable: {exc}") from exc
    if payload.get("feature_dim") != config.feature_dim:
        raise TrainingError("Checkpoint feature_dim does not match its config")
    if payload.get("hidden") != config.hidden:
        raise TrainingError("Checkpoint hidden does not match its config")
    weights = _strict_weights(payload["weights"], config.hidden, config.feature_dim)
    metrics = payload.get("metrics")
    if not isinstance(metrics, dict) or any(
        type(value) not in (int, float) for value in metrics.values()
    ):
        raise TrainingError("Checkpoint metrics must be numbers")
    created = payload.get("created")
    if not isinstance(created, str) or len(created) > 64:
        raise TrainingError("Checkpoint created must be a short ISO timestamp")
    model = TrainedRanker(
        name=name,
        config=config,
        w1=weights["w1"],
        b1=weights["b1"],
        v=weights["v"],
        b2=weights["b2"],
        metrics={key: float(value) for key, value in metrics.items()},
        created=created,
    )
    digest = payload.get("digest")
    if not isinstance(digest, str) or digest != model.identity():
        raise TrainingError(
            "Checkpoint digest does not match its weights; it was edited after training"
        )
    return model


def _refuse_without_examples(profile: str | Path) -> None:
    """Say a profile has nothing to learn from before indexing its sources.

    Loading a profile reads and checks every configured source, which for a
    specialist over a whole project takes minutes. A profile with no verified
    examples cannot train anything, so it is refused first, cheaply.
    """

    source = Path(profile)
    try:
        if source.stat().st_size > 256_000:
            raise TrainingError("Virtual-model profile exceeds 256 KB")
        payload = json.loads(source.read_text(encoding="utf-8"))
    except TrainingError:
        raise
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise TrainingError(f"Cannot read virtual-model profile: {exc}") from exc
    examples = payload.get("verified_examples") if isinstance(payload, dict) else None
    if not isinstance(examples, list) or not examples:
        raise TrainingError(
            "Training needs verified examples with source evidence; this profile "
            "has none. Add one with `kosika virtual-model add-example`."
        )


def build_training_set(
    profile: str | Path, config: TrainingConfig | None = None
) -> TrainingSet:
    """Turn a profile's verified examples into labelled (question, passage) pairs.

    Only examples a person verified, each with quoted evidence, are used. A
    passage containing a cited line is a positive; passages containing none of
    that example's citations are negatives, sampled deterministically so two
    runs on the same profile train on the same pairs.
    """

    chosen = config or TrainingConfig()
    _refuse_without_examples(profile)
    from .virtual_model import VirtualModelProvider

    specialist = VirtualModelProvider(_UnusedProvider(), profile)
    if not specialist.examples:
        raise TrainingError(
            "Training needs verified examples with source evidence; this profile "
            "has none. Add one with `kosika virtual-model add-example`."
        )
    passages = specialist.passages
    if len(passages) < 2:
        raise TrainingError(
            "Training needs at least two source passages, one cited and one not"
        )
    rng = random.Random(chosen.seed)
    pairs: list[TrainingPair] = []
    for index, example in enumerate(specialist.examples):
        cited: set[tuple[str, int]] = {
            (reference.source, reference.line) for reference in example.evidence
        }
        positive: list[Any] = []
        negative: list[Any] = []
        for passage in passages:
            lines = range(
                passage.start_line,
                passage.start_line + len(passage.text.splitlines()),
            )
            if any((passage.source, line) in cited for line in lines):
                positive.append(passage)
            else:
                negative.append(passage)
        for passage in positive:
            pairs.append(
                TrainingPair(
                    example.task, passage.text, 1, passage.citation, index
                )
            )
        for passage in rng.sample(
            negative, min(chosen.negative_ratio, len(negative))
        ):
            pairs.append(
                TrainingPair(
                    example.task, passage.text, 0, passage.citation, index
                )
            )
    if not any(pair.label == 1 for pair in pairs):
        raise TrainingError(
            "No checked passage contains a cited evidence line; check the profile's sources"
        )
    if len(pairs) > MAX_PAIRS:
        raise TrainingError(f"Training set exceeds {MAX_PAIRS} pairs")
    return TrainingSet(specialist.name, tuple(pairs))


def split_training_set(
    training: TrainingSet,
    holdout: int,
    seed: int = 7,
    min_examples: int = 2,
) -> tuple[TrainingSet, TrainingSet]:
    """Keep whole verified examples out of training, for an honest score.

    The split is by example, not by pair: a held-out task's cited passage must
    not also appear as a training positive. Which examples are held out is
    drawn from ``seed``, so the same profile and seed always score the same
    way.

    Two examples must remain for training by default. A ranker fitted on one
    example has seen one question, so its held-out score measures that example
    rather than any ability to generalize -- a number worth refusing to print.
    ``min_examples`` can lower the bar deliberately, never silently.
    """

    if type(holdout) is not int or holdout < 1:
        raise TrainingError("holdout must be a whole number of examples, at least 1")
    if type(min_examples) is not int or min_examples < 1:
        raise TrainingError(
            "min_examples must be a whole number of examples, at least 1"
        )
    indices = sorted({pair.example for pair in training.pairs})
    remaining = len(indices) - holdout
    if remaining < min_examples:
        raise TrainingError(
            f"holdout would leave {max(remaining, 0)} of {len(indices)} verified "
            f"examples for training, and at least {min_examples} is required; add "
            "verified examples, hold out fewer, or lower --min-train-examples"
        )
    shuffled = list(indices)
    random.Random(seed).shuffle(shuffled)
    held = set(shuffled[:holdout])
    fit = tuple(pair for pair in training.pairs if pair.example not in held)
    scored = tuple(pair for pair in training.pairs if pair.example in held)
    if not any(pair.label == 1 for pair in scored) or not any(
        pair.label == 0 for pair in scored
    ):
        raise TrainingError(
            "the held-out examples need both a cited and an uncited passage; "
            "raise --negatives or hold out more examples"
        )
    return TrainingSet(training.name, fit), TrainingSet(training.name, scored)


def evaluate_ranker(
    model: TrainedRanker, pairs: Sequence[TrainingPair]
) -> dict[str, float]:
    """Score a trained ranker on pairs it was not trained on.

    Accuracy is on the decision the ranker actually makes -- read this passage
    or not -- so a threshold over it is a claim about behaviour, not about a
    loss curve.
    """

    if not pairs:
        raise TrainingError("Evaluation needs at least one pair")
    found = _evaluate(
        model.w1, model.b1, model.v, model.b2, tuple(pairs), model.feature_dim
    )
    found["pairs"] = float(len(pairs))
    found["positives"] = float(sum(1 for pair in pairs if pair.label == 1))
    found["negatives"] = float(sum(1 for pair in pairs if pair.label == 0))
    return found


def evaluate_lexical(pairs: Sequence[TrainingPair]) -> dict[str, float]:
    """Score the rule Kosika used before training, on the same held-out pairs.

    The lexical retriever can only read a passage that shares a word with the
    question, and it reads as many passages as it was asked for. That is what
    ``VirtualModelProvider`` did before a ranker existed, so it is the honest
    baseline: a trained score is only interesting next to it.
    """

    if not pairs:
        raise TrainingError("Evaluation needs at least one pair")
    from .virtual_model import Passage, _rank

    groups: dict[str, list[TrainingPair]] = {}
    for pair in pairs:
        groups.setdefault(pair.query, []).append(pair)
    right = 0
    for query, group in groups.items():
        passages = tuple(
            Passage(pair.source or "pair", 1, pair.text) for pair in group
        )
        wanted = sum(1 for pair in group if pair.label == 1)
        chosen = {id(passage) for _, passage in _rank(query, passages)[:wanted]}
        for passage, pair in zip(passages, group):
            predicted = 1 if id(passage) in chosen else 0
            right += int(predicted == pair.label)
    return {
        "accuracy": right / len(pairs),
        "pairs": float(len(pairs)),
        "positives": float(sum(1 for pair in pairs if pair.label == 1)),
        "negatives": float(sum(1 for pair in pairs if pair.label == 0)),
    }


def train_ranker(
    training: TrainingSet, config: TrainingConfig | None = None
) -> TrainedRanker:
    """Learn the relevance weights with Adam, from verified pairs only.

    Every number here comes from the pairs; nothing is read from the model's
    own output, so the checkpoint records what a person verified rather than
    what a model guessed.
    """

    chosen = config or TrainingConfig()
    if not training.pairs:
        raise TrainingError("Training needs at least one pair")
    if not any(pair.label == 1 for pair in training.pairs):
        raise TrainingError("Training needs at least one relevant passage")
    if not any(pair.label == 0 for pair in training.pairs):
        raise TrainingError("Training needs at least one passage that is not relevant")
    rng = random.Random(chosen.seed)
    hidden, feature_dim = chosen.hidden, chosen.feature_dim
    w1 = [
        [rng.gauss(0.0, 0.02) for _ in range(feature_dim)] for _ in range(hidden)
    ]
    b1 = [0.0] * hidden
    v = [rng.gauss(0.0, 1.0 / math.sqrt(hidden)) for _ in range(hidden)]
    b2 = 0.0
    starting = _evaluate(w1, b1, v, b2, training.pairs, feature_dim)
    moment_w1 = [[0.0] * feature_dim for _ in range(hidden)]
    variance_w1 = [[0.0] * feature_dim for _ in range(hidden)]
    moment_b1 = [0.0] * hidden
    variance_b1 = [0.0] * hidden
    moment_v = [0.0] * hidden
    variance_v = [0.0] * hidden
    moment_b2 = 0.0
    variance_b2 = 0.0
    steps = 0
    history: list[float] = []
    order = list(training.pairs)
    for _ in range(chosen.epochs):
        rng.shuffle(order)
        for start in range(0, len(order), chosen.batch_size):
            batch = order[start : start + chosen.batch_size]
            grad_w1: dict[tuple[int, int], float] = {}
            grad_b1 = [0.0] * hidden
            grad_v = [0.0] * hidden
            grad_b2 = 0.0
            for pair in batch:
                sparse = features(pair.query, pair.text, feature_dim)
                probability, activations = _forward(w1, b1, v, b2, sparse)
                error = probability - pair.label
                for unit in range(hidden):
                    if activations[unit] <= 0.0:
                        continue
                    upstream = error * v[unit]
                    grad_b1[unit] += upstream
                    for index, value in sparse.items():
                        key = (unit, index)
                        grad_w1[key] = grad_w1.get(key, 0.0) + upstream * value
                for unit in range(hidden):
                    grad_v[unit] += error * activations[unit]
                grad_b2 += error
            scale = 1.0 / len(batch)
            steps += 1
            correction1 = 1.0 - _ADAM_BETA1**steps
            correction2 = 1.0 - _ADAM_BETA2**steps
            for (unit, index), gradient in grad_w1.items():
                value = gradient * scale
                moment = moment_w1[unit][index] = (
                    _ADAM_BETA1 * moment_w1[unit][index] + (1.0 - _ADAM_BETA1) * value
                )
                variance = variance_w1[unit][index] = (
                    _ADAM_BETA2 * variance_w1[unit][index]
                    + (1.0 - _ADAM_BETA2) * value * value
                )
                adjusted = (moment / correction1) / (
                    math.sqrt(variance / correction2) + _ADAM_EPSILON
                )
                w1[unit][index] -= chosen.learning_rate * adjusted
            for unit in range(hidden):
                value = grad_b1[unit] * scale
                moment_b1[unit] = (
                    _ADAM_BETA1 * moment_b1[unit] + (1.0 - _ADAM_BETA1) * value
                )
                variance_b1[unit] = (
                    _ADAM_BETA2 * variance_b1[unit]
                    + (1.0 - _ADAM_BETA2) * value * value
                )
                b1[unit] -= chosen.learning_rate * (
                    (moment_b1[unit] / correction1)
                    / (math.sqrt(variance_b1[unit] / correction2) + _ADAM_EPSILON)
                )
                value = grad_v[unit] * scale
                moment_v[unit] = (
                    _ADAM_BETA1 * moment_v[unit] + (1.0 - _ADAM_BETA1) * value
                )
                variance_v[unit] = (
                    _ADAM_BETA2 * variance_v[unit]
                    + (1.0 - _ADAM_BETA2) * value * value
                )
                v[unit] -= chosen.learning_rate * (
                    (moment_v[unit] / correction1)
                    / (math.sqrt(variance_v[unit] / correction2) + _ADAM_EPSILON)
                )
            value = grad_b2 * scale
            moment_b2 = _ADAM_BETA1 * moment_b2 + (1.0 - _ADAM_BETA1) * value
            variance_b2 = (
                _ADAM_BETA2 * variance_b2 + (1.0 - _ADAM_BETA2) * value * value
            )
            b2 -= chosen.learning_rate * (
                (moment_b2 / correction1)
                / (math.sqrt(variance_b2 / correction2) + _ADAM_EPSILON)
            )
        history.append(
            _evaluate(w1, b1, v, b2, training.pairs, feature_dim)["loss"]
        )
    final = _evaluate(w1, b1, v, b2, training.pairs, feature_dim)
    metrics = {
        "pairs": float(len(training.pairs)),
        "positives": float(training.positives),
        "negatives": float(training.negatives),
        "initial_loss": starting["loss"],
        "loss": final["loss"],
        "accuracy": final["accuracy"],
        "mean_positive": final["mean_positive"],
        "mean_negative": final["mean_negative"],
    }
    model = TrainedRanker(
        name=training.name,
        config=chosen,
        w1=w1,
        b1=b1,
        v=v,
        b2=b2,
        metrics=metrics,
        history=history,
    )
    return model
