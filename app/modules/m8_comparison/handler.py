from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.errors import Problem
from app.modules.m8_comparison import service
from app.modules.m8_comparison.export import attachment, publication_attachment
from app.store import Store, get_store

router = APIRouter(prefix='/api/comparisons', tags=['改造差异'])
publications = APIRouter(prefix='/api/publications', tags=['差异发布'])
archive_basis = APIRouter(prefix='/api/runs', tags=['改造差异'])


class ComparisonInput(BaseModel):
    after_run_id: str
    before_run_id: str | None = None
    baseline: dict[str, str] | None = None


class PublicationInput(BaseModel):
    comparison_id: str
    scope: str = 'confirmed'


class BasisInput(BaseModel):
    basis: str
    note: str | None = None


@router.post('')
def create(spec: ComparisonInput, store: Store = Depends(get_store)):
    return service.create(store, spec.after_run_id, spec.before_run_id, spec.baseline)


@router.get('')
def index(store: Store = Depends(get_store)):
    return service.history(store)


@router.get('/{comparison_id}')
def detail(comparison_id: str, store: Store = Depends(get_store)):
    return service.read(store, comparison_id)


@router.get('/{comparison_id}/export')
def export(comparison_id: str, store: Store = Depends(get_store)):
    return attachment(store, comparison_id)


@publications.post('')
def publish(spec: PublicationInput, store: Store = Depends(get_store)):
    return service.publish(store, spec.comparison_id, spec.scope)


@publications.get('')
def publication_index(store: Store = Depends(get_store)):
    return service.publication_history(store)


@publications.get('/latest')
def latest(store: Store = Depends(get_store)):
    return service.latest_publication(store)


@publications.get('/{publication_id}')
def publication(publication_id: str, store: Store = Depends(get_store)):
    return service.read_publication(store, publication_id)


@publications.get('/{publication_id}/export')
def publication_export(publication_id: str, store: Store = Depends(get_store)):
    return publication_attachment(store, publication_id)


@archive_basis.post('/{run_id}/basis')
def declare_basis(run_id: str, spec: BasisInput, store: Store = Depends(get_store)):
    if not spec.basis.strip():
        raise Problem('BASIS_INVALID', '口径标记不能为空')
    return service.declare_basis(store, run_id, spec.basis, spec.note)
