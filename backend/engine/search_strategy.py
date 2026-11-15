"""Finite ask/tell candidate selection inside the existing execution owner.

The in-memory study is a sampler, not a scheduler or durable optimizer resume.
Only the coordinator calls tell after its owned completed receipt is durable.
"""
from __future__ import annotations

from hashlib import sha256
import itertools
import json
import math

OPTUNA_VERSION = '5.0.0'
SAMPLER_OPTIONS = {'n_startup_trials': 4, 'n_ei_candidates': 24,
                   'multivariate': False, 'constant_liar': False}
PRUNING = {'available': False, 'reason': 'No validated per-epoch heldout progress contract across model families'}
DUPLICATE_ASK_LIMIT = 16


def configuration_key(config):
    """Model controls only: callers attach the ordinal seed after deduplication."""
    def canonical(value):
        # Preserve the original Cartesian equality for numeric alternatives
        # (learning_rate=1 and 1.0 execute the same configuration).
        if isinstance(value, float) and math.isfinite(value) and value.is_integer():return int(value)
        if isinstance(value, dict):return {key:canonical(item) for key,item in value.items()}
        if isinstance(value, (list, tuple)):return [canonical(item) for item in value]
        return value
    return json.dumps(canonical(config), sort_keys=True, separators=(',', ':'), allow_nan=False)


def _load_optuna():
    try:
        import optuna
    except ImportError as exc:
        raise RuntimeError('Adaptive search requires bundled Optuna 5.0.0 in the coordinator runtime') from exc
    if optuna.__version__ != OPTUNA_VERSION:
        raise RuntimeError(f'Adaptive search requires Optuna {OPTUNA_VERSION}; found {optuna.__version__}')
    return optuna


def resolve_strategy(requested, mode, max_trials):
    if requested not in ('auto', 'deterministic', 'adaptive'):
        raise ValueError('Unknown search strategy')
    if mode != 'search': return 'deterministic'
    return ('adaptive' if max_trials >= 8 else 'deterministic') if requested == 'auto' else requested


def capabilities():
    error = None
    try: _load_optuna()
    except RuntimeError as exc: error = str(exc)
    return {'strategies': ['auto', 'deterministic', 'adaptive'], 'auto_adaptive_min_trials': 8,
            'adaptive_available': error is None, 'unavailable_reason': error,
            'optuna_version': OPTUNA_VERSION, 'sampler_execution': 'coordinator',
            'remote_worker_requires_sampler': False, 'pruning': dict(PRUNING)}


def enumerate_domain(domain, base):
    """Preserve diagonal-first coverage without materializing the Cartesian product."""
    keys = tuple(domain); values = list(domain.values()); seen = set()
    diagonals = (tuple(v[i % len(v)] for v in values) for i in range(max(map(len, values))))
    for row in itertools.chain(diagonals, itertools.product(*values)):
        config = {**base, **dict(zip(keys, row))}
        key = configuration_key(config)
        if key in seen: continue
        seen.add(key)
        yield config


class CandidateSearch:
    def __init__(self, domain, base, *, requested, mode, max_trials, seed):
        # Domain validation belongs to the task adapter; this boundary additionally
        # requires bounded JSON categorical alternatives, never numeric ranges.
        if not isinstance(domain, dict) or not domain:
            raise ValueError('Search requires a finite categorical domain')
        self.domain = {}
        for key, choices in domain.items():
            if not isinstance(key, str) or not isinstance(choices, list) or not 1 <= len(choices) <= 16:
                raise ValueError('Search requires bounded categorical choices')
            unique = {configuration_key(value): value for value in choices}
            self.domain[key] = list(unique.values())
        self.base = dict(base); self.seen = set(); self.proposal_count = 0
        self.enumeration = enumerate_domain(self.domain, self.base)
        resolved = resolve_strategy(requested, mode, max_trials)
        self.optuna = _load_optuna() if resolved == 'adaptive' else None
        self.study = None
        if self.optuna:
            sampler = self.optuna.samplers.TPESampler(seed=seed, **SAMPLER_OPTIONS)
            self.study = self.optuna.create_study(direction='minimize', sampler=sampler)
        self.identity = {'contract_version': 1, 'requested_strategy': requested, 'resolved_strategy': resolved,
            'domain_sha256': sha256(configuration_key(self.domain).encode()).hexdigest(),
            'optuna_version': OPTUNA_VERSION if self.optuna else None,
            'sampler': 'TPESampler' if self.optuna else 'diagonal_cartesian',
            'sampler_options': dict(SAMPLER_OPTIONS) if self.optuna else {}, 'seed': seed,
            'sampler_execution': 'coordinator', 'continuation': 'new_study_with_verified_completed_reuse',
            'pruning': dict(PRUNING)}

    def _sample(self):
        token = self.study.ask()
        config = dict(self.base)
        for key, values in self.domain.items():
            # JSON encoding permits rectangular image-size categories while keeping
            # the sampler's categorical distributions immutable and primitive.
            encoded = token.suggest_categorical(key, [configuration_key(value) for value in values])
            config[key] = json.loads(encoded)
        return config, token

    def ask(self):
        token = None; source = 'enumeration'
        if self.study:
            for _ in range(DUPLICATE_ASK_LIMIT):
                config, token = self._sample()
                if configuration_key(config) not in self.seen:
                    source = 'sampler'; break
                self.tell(token)  # Duplicate proposals spend no execution budget.
            else: token = None
        if token is None:
            config = next(row for row in self.enumeration if configuration_key(row) not in self.seen)
            if self.study:
                self.study.enqueue_trial({key: configuration_key(config[key]) for key in self.domain})
                config, token = self._sample()
        self.seen.add(configuration_key(config))
        proposal = {'proposal_ordinal': self.proposal_count, 'proposal_source': source,
                    'sampler_trial_number': token.number if token else None}
        self.proposal_count += 1
        return config, token, proposal

    def tell(self, token, objective=None):
        if self.study is None or token is None: return
        if objective is None:
            self.study.tell(token, state=self.optuna.trial.TrialState.FAIL)
        else:
            if isinstance(objective, bool) or not isinstance(objective, (int, float)) or not math.isfinite(objective):
                raise ValueError('Sampler feedback requires a finite measured objective')
            self.study.tell(token, float(objective))
