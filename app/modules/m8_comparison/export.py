from fastapi.responses import Response

from app.evidence import canonical
from app.modules.m8_comparison import service


def attachment(store, comparison_id):
    body = canonical(service.read(store, comparison_id))
    return Response(body, media_type='application/json', headers={
        'Content-Disposition': f'attachment; filename="retrofit-diff-{comparison_id[:12]}.json"',
    })


def publication_attachment(store, publication_id):
    body = canonical(service.read_publication(store, publication_id))
    return Response(body, media_type='application/json', headers={
        'Content-Disposition': f'attachment; filename="retrofit-diff-release-{publication_id[:12]}.json"',
    })
