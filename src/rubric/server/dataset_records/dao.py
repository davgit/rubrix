from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Union

from fastapi import Depends
from pydantic import Field
from pydantic.main import BaseModel
from rubric.server.commons.es_wrapper import ElasticsearchWrapper, create_es_wrapper
from rubric.server.commons.helpers import unflatten_dict
from rubric.server.datasets.dao import (
    DATASETS_RECORDS_INDEX_NAME,
    dataset_records_index,
)
from rubric.server.datasets.model import ObservationDatasetDB

from . import es_helpers
from .es_helpers import aggregations, parse_aggregations, parse_tasks_aggregations
from .model.search import WordCloudAggregations

ES_ANALYZER_FOR_LANG = {
    "es": "spanish",
    "en": "english",
    "fr": "french",
    "de": "german",
}

SUPPORTED_LANGUAGES = list(ES_ANALYZER_FOR_LANG.keys())


DATASETS_RECORDS_INDEX_TEMPLATE = {
    "index_patterns": [DATASETS_RECORDS_INDEX_NAME.format("*")],
    "mappings": {
        "properties": {
            **{
                f"language_words.{lang}": {
                    "type": "text",
                    "analyzer": ES_ANALYZER_FOR_LANG[lang],
                    "fielddata": True,
                }
                for lang in SUPPORTED_LANGUAGES
            },
            "event_timestamp": {"type": "date"},
            "tokens": {"type": "text"},
        },
        "dynamic_templates": [
            {"text": {"path_match": "text.*", "mapping": {"type": "text"}}},
            {
                "status": {
                    "path_match": "*.status",
                    "mapping": {
                        "type": "keyword",
                    },
                }
            },
            {
                "predicted": {
                    "path_match": "*.predicted",
                    "mapping": {
                        "type": "keyword",
                    },
                }
            },
            {
                "strings": {
                    "match_mapping_type": "string",
                    "mapping": {"type": "keyword"},
                }
            },
        ],
    },
}


class LanguageWords(BaseModel):
    """

    Record text content by language

    es: str
        Text record content for spanish
    en: str
        Text record content for english
    ge: str
        Text record content for german
    fr: str
        Text record content for french

    """

    es: str = None
    en: str = None
    ge: str = None
    fr: str = None


class MultiTaskRecordDB(BaseModel):
    """
    A general dataset record stored model. All task records will
    be stored as part of dataset multitask record

    Attributes:
    -----------

    id: Union[int, str]
        The data record id

    metadata: Dict[str, Any]
        The record metadata

    event_timestamp: Optional[datetime]
        Record event triggered timestamp

    owner: Optional[str]
        The dataset/record owner

    last_updated: datetime
        Last record modification timestamp

    text: Dict[str, Any]
        The input text data

    tokens: List[str]
        The token list for token classification

    raw_text: str
        The original text for the provided tokens list

    tasks: Dict[str, Dict[str, Any]]
        A dictionary of task info. The key corresponds to
        value representation of convenient `TaskType`

    language_words: LanguageWords
        The record textual content contextualized by lang
    """

    id: Union[int, str]
    metadata: Dict[str, Any] = Field(default=None)
    event_timestamp: Optional[datetime] = None
    owner: Optional[str] = None
    last_updated: datetime = Field(default_factory=datetime.utcnow)

    text: Dict[str, Any] = None

    tokens: List[str] = None
    raw_text: str = None

    tasks: Dict[str, Dict[str, Any]]

    language_words: LanguageWords

    @staticmethod
    def field_name_for_task(task: str) -> str:
        """Generate de es field name base for task fields"""
        return f"tasks.{task}"


class TaskSearch(BaseModel):
    """
    Partial elasticsearch search params for a concrete task

    Attributes:
    -----------

    task:str
        The task name

    filters: List[Dict[str, Any]]
        Elasticsearch filters (queries) to apply in the task context

    aggregations: Dict[str, Any]
        Elasticsearch aggregations to apply in the task context

    """

    task: str
    filters: List[Dict[str, Any]] = Field(default_factory=list)
    aggregations: Dict[str, Any] = Field(default_factory=dict)


class MultiTaskSearch(BaseModel):
    """
    Multi task search data model

    Attributes:
    -----------

    text_query: Optional[Dict[str, Any]]
        Elasticsearch text query filters

    metadata_filters: Optional[Dict[str, Union[str, List[str]]]]
        Elasticsearch metadata query filters

    tasks: List[TaskSearch]
        Task search configuration list

    """

    text_query: Optional[Dict[str, Any]] = None
    metadata_filters: Optional[Dict[str, Union[str, List[str]]]] = None
    tasks: List[TaskSearch] = Field(default_factory=list)

    @property
    def query(self) -> Dict[str, Any]:
        """Compound elasticsearch multitask query"""
        all_filters = es_helpers.filters.metadata(self.metadata_filters)
        for task_search in self.tasks:
            if task_search.filters:
                for field_name in [
                    MultiTaskRecordDB.field_name_for_task(task_search.task)
                ]:
                    all_filters.append(es_helpers.filters.exists_field(field_name))
                    all_filters.extend(
                        es_helpers.prefix_query_fields(
                            task_search.filters, prefix=field_name
                        )
                    )

        return {
            "bool": {
                "must": self.text_query or {"match_all": {}},
                "filter": {
                    "bool": {
                        "should": all_filters,
                        "minimum_should_match": len(all_filters),
                    }
                },
            }
        }

    def aggregations(self, metadata_fields: Dict[str, str]) -> Dict[str, Any]:
        """
        Build elasticsearch aggregations

        Parameters
        ----------
        metadata_fields:
            Metadata fields which include aggregations

        Returns
        -------
            The elasticsearch multitask aggregation

        """
        tasks_aggregations = {
            key: value
            for task_search in self.tasks
            for key, value in es_helpers.prefix_aggregations_fields(
                task_search.aggregations,
                prefix=MultiTaskRecordDB.field_name_for_task(task_search.task),
            ).items()
        }
        return {
            **tasks_aggregations,
            **aggregations.language_words_cloud(langs=SUPPORTED_LANGUAGES),
            **aggregations.custom_fields(metadata_fields),
        }


class MultiTaskSearchResult(BaseModel):
    """
    Multi task search result data model

    Attributes:
    -----------

    total: int
        The total of query results

    records: List[MultiTaskRecordDB]
        List of records retrieved for the pagination configuration

    aggregations: Optional[Dict[str, Dict[str, Any]]]
        The query aggregations. Optional

    language_word_clouds: Optional[WordCloudAggregations]
        The word cloud aggregations grouped by stored languages

    metadata: Optional[Dict[str, int]]
        Metadata fields aggregations

    """

    total: int
    records: List[MultiTaskRecordDB]
    aggregations: Optional[Dict[str, Dict[str, Any]]] = Field(default_factory=dict)
    language_word_clouds: Optional[WordCloudAggregations] = None
    metadata: Optional[Dict[str, int]] = None


class DatasetRecordsDAO:
    """Datasets records DAO"""

    def __init__(self, es: ElasticsearchWrapper):
        self._es = es

        self._es.create_index_template(
            name=DATASETS_RECORDS_INDEX_NAME,
            template=DATASETS_RECORDS_INDEX_TEMPLATE,
            force_recreate=True,
        )
        self._es.create_index(DATASETS_RECORDS_INDEX_NAME)

    def add_records(
        self,
        dataset: ObservationDatasetDB,
        records: Iterable[MultiTaskRecordDB],
    ) -> int:
        """
        Add records to dataset

        Parameters
        ----------
        dataset:
            The dataset
        records:
            The list of records

        Returns
        -------
            The number of failed records

        """
        return self._es.add_documents(
            index=dataset_records_index(dataset.id),
            documents=[r.dict(by_alias=True, exclude_none=True) for r in records],
            doc_id=lambda r: r.get("id"),
        )

    def search_records(
        self,
        dataset: ObservationDatasetDB,
        search: MultiTaskSearch,
        size: int = 100,
        record_from: int = 0,
        sort: Optional[List[Dict[str, Any]]] = None,
    ) -> MultiTaskSearchResult:
        """
        Search records under a dataset given a search parameters.

        Parameters
        ----------
        dataset:
            The dataset
        search:
            The search params
        size:
            Number of records to retrieve (for pagination)
        record_from:
            Record from witch retrieve records (for pagination)
        sort:
            Sort parameters criteria to apply

        Returns
        -------
            The search result

        """

        records_index = dataset_records_index(dataset.id)
        metadata_fields = self._es.get_field_mapping(
            index=records_index, field_name="metadata.*"
        )
        search_aggregations = (
            search.aggregations(metadata_fields) if record_from == 0 else None
        )
        sort = sort or [{"_id": {"order": "asc"}}]

        es_query = {
            "size": size,
            "from": record_from,
            "query": {"bool": {"must": search.query}},
            "sort": sort,
            "aggs": search_aggregations or {},
        }

        results = self._es.search(index=records_index, query=es_query, size=size)

        hits = results["hits"]
        total = hits["total"]
        docs = hits["hits"]
        search_aggregations = unflatten_dict(results.get("aggregations", {}))

        result = MultiTaskSearchResult(
            total=total,
            records=[MultiTaskRecordDB.parse_obj(doc["_source"]) for doc in docs],
        )
        if search_aggregations:
            result.aggregations = parse_tasks_aggregations(search_aggregations["tasks"])
            result.language_word_clouds = WordCloudAggregations(
                **parse_aggregations(search_aggregations.get("language_words"))
            )
            result.metadata = parse_aggregations(search_aggregations.get("metadata"))
        return result

    def scan_dataset(
        self,
        dataset: ObservationDatasetDB,
        search: MultiTaskSearch,
    ) -> Iterable[MultiTaskRecordDB]:
        """
        Iterates over a dataset records

        Parameters
        ----------
        dataset:
            The dataset
        search:
            The search parameters

        Returns
        -------
            An iterable over found dataset records
        """
        es_query = {
            "query": search.query,
        }
        docs = self._es.list_documents(
            dataset_records_index(dataset.id), query=es_query
        )
        return (MultiTaskRecordDB.parse_obj(doc["_source"]) for doc in docs)


_instance: Optional[DatasetRecordsDAO] = None


def create_dataset_records_dao(
    es: ElasticsearchWrapper = Depends(create_es_wrapper),
) -> DatasetRecordsDAO:
    """
    Creates a dataset records dao instance

    Parameters
    ----------
    es:
        The elasticserach wrapper dependency

    """
    global _instance
    if not _instance:
        _instance = DatasetRecordsDAO(es)
    return _instance