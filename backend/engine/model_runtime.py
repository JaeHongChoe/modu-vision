"""Optional compiled neural execution without changing model reconstruction."""
from contextvars import ContextVar

active_model_runtime=ContextVar('active_model_runtime',default=None)


def runtime_model(model,checkpoint,role='model'):
    session=active_model_runtime.get()
    return session.model(model,checkpoint,role) if session is not None else model


def runtime_anomaly(detector,checkpoint):
    session=active_model_runtime.get()
    if session is None:return detector
    if getattr(detector,'model_metadata',{}).get('detector_type')=='dino_synthetic':
        detector._logits=session.model(detector.model,checkpoint,'dino_logits')
    else:
        detector.feature_extractor=session.model(detector.feature_extractor,checkpoint,'features')
    return detector
