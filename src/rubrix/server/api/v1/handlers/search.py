import dataclasses
import itertools
from typing import Iterable, List, Optional

from fastapi import APIRouter, Body, Depends, Query, Security
from starlette.responses import StreamingResponse

from rubrix.server.api.v2.config.factory import __all__ as all_tasks
from rubrix.server.api.v2.models.commons.params import (
    NameEndpointHandlerParams,
    PaginationParams,
)
from rubrix.server.api.v2.models.search import BaseSearchResults
from rubrix.server.datasets.service import DatasetsService
from rubrix.server.security import auth
from rubrix.server.security.model import User
from rubrix.server.tasks.commons import BaseRecord, SortableField
from rubrix.server.tasks.commons.helpers import takeuntil
from rubrix.server.tasks.commons.service import TaskService
from rubrix.server.tasks.search.service import SearchRecordsService


def _streaming_response(
    data_stream: Iterable[BaseRecord],
    chunk_size: int = 1000,
    limit: Optional[int] = None,
) -> StreamingResponse:
    async def stream_generator(stream):
        """Converts dataset scan into a text stream"""

        def grouper(n, iterable, fillvalue=None):
            args = [iter(iterable)] * n
            return itertools.zip_longest(fillvalue=fillvalue, *args)

        if limit:
            stream = takeuntil(stream, limit=limit)

        for batch in grouper(
            n=chunk_size,
            iterable=stream,
        ):
            filtered_records = filter(lambda r: r is not None, batch)
            yield "\n".join(
                map(
                    lambda r: r.json(by_alias=True, exclude_none=True), filtered_records
                )
            ) + "\n"

    return StreamingResponse(
        stream_generator(data_stream), media_type="application/json"
    )


@dataclasses.dataclass
class EndpointParams(NameEndpointHandlerParams):
    include_field: List[str] = Query(
        None, description="Only provided fields will be returned in records"
    )
    exclude_field: List[str] = Query(
        None, description="Exclude provided field from records"
    )


def configure_router() -> APIRouter:

    router = APIRouter(tags=["v2 - Search"])

    for cfg in all_tasks:
        base_endpoint = f"/{cfg.task}/{{name}}"
        service_class = cfg.service_class

        search_results_class = type(
            f"SearchResults_{cfg.task}", (BaseSearchResults[cfg.record_class],), {}
        )

        @router.post(
            base_endpoint + "/search",
            name=f"Search records in a {cfg.task} dataset",
            operation_id=f"{cfg.task}/search_records",
            response_model=search_results_class,
            response_model_exclude_none=True,
        )
        async def search_records(
            query: cfg.query_class = Body(...),
            sort: List[SortableField] = Body(...),
            params: EndpointParams = Depends(),
            pagination: PaginationParams = Depends(),
            datasets: DatasetsService = Depends(DatasetsService.get_instance),
            service: TaskService = Depends(service_class.get_instance),
            user: User = Security(auth.get_user, scopes=["read"]),
        ) -> search_results_class:

            dataset = datasets.find_by_name(
                user=user,
                name=params.name,
                task=cfg.task,
                workspace=params.common.workspace,
            )

            results = await service.search(
                dataset=dataset,
                query=query,
                sort_by=sort,
                record_from=pagination.from_,
                size=pagination.limit,
            )

            return search_results_class(
                total=results.total,
                records=results.records,
            )

        @router.post(
            base_endpoint + "/view",
            name=f"View projection of a {cfg.task} dataset",
            operation_id=f"{cfg.task}/view_dataset",
            response_model=Iterable[cfg.record_class],
            response_model_exclude_none=True,
        )
        async def view_dataset(
            query: cfg.query_class = Body(...),
            params: EndpointParams = Depends(),
            limit: Optional[int] = Query(
                None, description="Limit loaded records", gt=0
            ),
            datasets: DatasetsService = Depends(DatasetsService.get_instance),
            search: SearchRecordsService = Depends(SearchRecordsService.get_instance),
            user: User = Security(auth.get_user, scopes=["read"]),
        ) -> StreamingResponse:
            dataset = datasets.find_by_name(
                user=user,
                name=params.name,
                task=cfg.task,
                workspace=params.common.workspace,
            )

            return _streaming_response(
                limit=limit,
                data_stream=search.scan_records(
                    dataset,
                    query=query,
                    record_type=cfg.record_class,
                ),
            )

    return router


__router__ = configure_router()
