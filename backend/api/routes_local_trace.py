"""Current authenticated project/actor diagnostic read. The epoch adds isolation, never authority."""
from fastapi import APIRouter, HTTPException, Request
from backend.contracts.context import get_project_context
from backend.engine.local_trace import EPOCH, local_traces

router = APIRouter(prefix='/api/local-trace', tags=['diagnostics'])


@router.get('')
def read_local_trace(request: Request):
    context = get_project_context(request)
    epoch = request.headers.get('x-vision-trace-scope', '')
    if not EPOCH.fullmatch(epoch):
        raise HTTPException(400, 'Current local trace scope epoch required')
    return local_traces.read(context, epoch)
