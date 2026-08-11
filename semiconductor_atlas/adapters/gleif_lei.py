"""Strict offline parser for archived GLEIF Level 1 JSON:API responses."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any


GLEIF_LEI_RESOURCE_TYPE = "lei-records"
GLEIF_LEI_CANONICAL_FORMAT = "gleif-lei-jsonapi-level-1-v1"

_LEI_RE = re.compile(r"^[0-9A-Z]{20}$")
_AUTHORITY_RE = re.compile(r"^RA[0-9]{6}$")
_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")


@dataclass(frozen=True, slots=True)
class GLEIFName:
    name: str
    language: str | None
    name_type: str | None


@dataclass(frozen=True, slots=True)
class GLEIFAddress:
    language: str | None
    address_lines: tuple[str, ...]
    address_number: str | None
    address_number_within_building: str | None
    mail_routing: str | None
    city: str
    region: str | None
    country: str
    postal_code: str | None


@dataclass(frozen=True, slots=True)
class GLEIFAuthority:
    authority_id: str | None
    other: str | None


@dataclass(frozen=True, slots=True)
class GLEIFRegistration:
    initial_registration_date: str
    last_update_date: str
    status: str
    next_renewal_date: str
    managing_lou: str
    corroboration_level: str | None
    validated_at: GLEIFAuthority | None
    validated_as: str | None
    other_validation_authorities: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class GLEIFLEIRecord:
    lei: str
    legal_name: GLEIFName
    other_names: tuple[GLEIFName, ...]
    transliterated_other_names: tuple[GLEIFName, ...]
    legal_address: GLEIFAddress
    headquarters_address: GLEIFAddress
    jurisdiction: str | None
    category: str | None
    sub_category: str | None
    status: str
    registration_authority: GLEIFAuthority | None
    registration_entity_id: str | None
    legal_form: dict[str, Any] | None
    creation_date: str | None
    expiration: dict[str, Any] | None
    successor_entity: dict[str, Any] | None
    successor_entities: tuple[dict[str, Any], ...]
    event_groups: tuple[dict[str, Any], ...]
    registration: GLEIFRegistration


@dataclass(frozen=True, slots=True)
class GLEIFLEISnapshot:
    golden_copy_publish_date: str
    records: tuple[GLEIFLEIRecord, ...]
    raw_sha256: str
    raw_bytes: int


class _DuplicateJSONKey(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJSONKey(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant {value}")


def _object(value: object, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be a JSON object")
    return value


def _array(value: object, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{context} must be a JSON array")
    return value


def _required_text(value: object, context: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(ord(character) < 0x20 for character in value)
    ):
        raise ValueError(f"{context} must be a non-empty clean string")
    return value


def _optional_text(value: object, context: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, context)


def _timestamp(value: object, context: str) -> str:
    text = _required_text(value, context)
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as error:
        raise ValueError(f"{context} must be an ISO 8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{context} must include a timezone")
    return text


def _optional_timestamp(value: object, context: str) -> str | None:
    if value is None:
        return None
    return _timestamp(value, context)


def _lei(value: object, context: str) -> str:
    text = _required_text(value, context)
    if not _LEI_RE.fullmatch(text):
        raise ValueError(
            f"{context} must be a 20-character uppercase alphanumeric LEI"
        )
    return text


def _parse_name(
    value: object,
    context: str,
    *,
    require_language: bool,
    require_type: bool,
) -> GLEIFName:
    payload = _object(value, context)
    expected = {"name", "language"} | ({"type"} if require_type else set())
    if set(payload) != expected:
        raise ValueError(f"{context} has an invalid name structure")
    language = (
        _required_text(payload["language"], f"{context}.language")
        if require_language
        else _optional_text(payload["language"], f"{context}.language")
    )
    return GLEIFName(
        name=_required_text(payload["name"], f"{context}.name"),
        language=language,
        name_type=(
            _required_text(payload["type"], f"{context}.type")
            if require_type
            else None
        ),
    )


def _parse_names(value: object, context: str) -> tuple[GLEIFName, ...]:
    return tuple(
        _parse_name(
            item,
            f"{context}[{index}]",
            require_language=False,
            require_type=True,
        )
        for index, item in enumerate(_array(value, context))
    )


def _parse_address(value: object, context: str) -> GLEIFAddress:
    payload = _object(value, context)
    expected = {
        "language",
        "addressLines",
        "addressNumber",
        "addressNumberWithinBuilding",
        "mailRouting",
        "city",
        "region",
        "country",
        "postalCode",
    }
    if set(payload) != expected:
        raise ValueError(f"{context} has an invalid address structure")
    address_lines = tuple(
        _required_text(item, f"{context}.addressLines[{index}]")
        for index, item in enumerate(
            _array(payload["addressLines"], f"{context}.addressLines")
        )
    )
    if not address_lines:
        raise ValueError(f"{context}.addressLines must not be empty")
    country = _required_text(payload["country"], f"{context}.country")
    if not _COUNTRY_RE.fullmatch(country):
        raise ValueError(f"{context}.country must be an uppercase alpha-2 code")
    return GLEIFAddress(
        language=_optional_text(payload["language"], f"{context}.language"),
        address_lines=address_lines,
        address_number=_optional_text(
            payload["addressNumber"], f"{context}.addressNumber"
        ),
        address_number_within_building=_optional_text(
            payload["addressNumberWithinBuilding"],
            f"{context}.addressNumberWithinBuilding",
        ),
        mail_routing=_optional_text(
            payload["mailRouting"], f"{context}.mailRouting"
        ),
        city=_required_text(payload["city"], f"{context}.city"),
        region=_optional_text(payload["region"], f"{context}.region"),
        country=country,
        postal_code=_optional_text(
            payload["postalCode"], f"{context}.postalCode"
        ),
    )


def _parse_authority(value: object, context: str) -> GLEIFAuthority | None:
    if value is None:
        return None
    payload = _object(value, context)
    if set(payload) != {"id", "other"}:
        raise ValueError(f"{context} has an invalid authority structure")
    authority_id = _optional_text(payload["id"], f"{context}.id")
    if authority_id is not None and not _AUTHORITY_RE.fullmatch(authority_id):
        raise ValueError(f"{context}.id must be an RA code")
    other = _optional_text(payload["other"], f"{context}.other")
    if authority_id is None and other is None:
        return GLEIFAuthority(None, None)
    return GLEIFAuthority(authority_id, other)


def _object_tuple(value: object, context: str) -> tuple[dict[str, Any], ...]:
    return tuple(
        _object(item, f"{context}[{index}]")
        for index, item in enumerate(_array(value, context))
    )


def _optional_object(value: object, context: str) -> dict[str, Any] | None:
    if value is None:
        return None
    return _object(value, context)


def _parse_registration(value: object, context: str) -> GLEIFRegistration:
    payload = _object(value, context)
    required = {
        "initialRegistrationDate",
        "lastUpdateDate",
        "status",
        "nextRenewalDate",
        "managingLou",
        "corroborationLevel",
        "validatedAt",
        "validatedAs",
        "otherValidationAuthorities",
    }
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"{context} is missing fields: {missing!r}")
    return GLEIFRegistration(
        initial_registration_date=_timestamp(
            payload["initialRegistrationDate"],
            f"{context}.initialRegistrationDate",
        ),
        last_update_date=_timestamp(
            payload["lastUpdateDate"], f"{context}.lastUpdateDate"
        ),
        status=_required_text(payload["status"], f"{context}.status"),
        next_renewal_date=_timestamp(
            payload["nextRenewalDate"], f"{context}.nextRenewalDate"
        ),
        managing_lou=_lei(payload["managingLou"], f"{context}.managingLou"),
        corroboration_level=_optional_text(
            payload["corroborationLevel"], f"{context}.corroborationLevel"
        ),
        validated_at=_parse_authority(
            payload["validatedAt"], f"{context}.validatedAt"
        ),
        validated_as=_optional_text(
            payload["validatedAs"], f"{context}.validatedAs"
        ),
        other_validation_authorities=_object_tuple(
            payload["otherValidationAuthorities"],
            f"{context}.otherValidationAuthorities",
        ),
    )


def _validate_successor(value: dict[str, Any], context: str) -> None:
    if "lei" in value and value["lei"] is not None:
        _lei(value["lei"], f"{context}.lei")
    if "name" in value and value["name"] is not None:
        _required_text(value["name"], f"{context}.name")
    if "language" in value and value["language"] is not None:
        _required_text(value["language"], f"{context}.language")


def _parse_record(value: object, index: int) -> GLEIFLEIRecord:
    context = f"data[{index}]"
    resource = _object(value, context)
    if resource.get("type") != GLEIF_LEI_RESOURCE_TYPE:
        raise ValueError(f"{context}.type must be {GLEIF_LEI_RESOURCE_TYPE!r}")
    resource_lei = _lei(resource.get("id"), f"{context}.id")
    attributes = _object(resource.get("attributes"), f"{context}.attributes")
    attribute_lei = _lei(attributes.get("lei"), f"{context}.attributes.lei")
    if resource_lei != attribute_lei:
        raise ValueError(f"{context}.id and attributes.lei must match")
    entity = _object(attributes.get("entity"), f"{context}.attributes.entity")

    required_entity_fields = {
        "legalName",
        "otherNames",
        "transliteratedOtherNames",
        "legalAddress",
        "headquartersAddress",
        "registeredAt",
        "registeredAs",
        "jurisdiction",
        "category",
        "status",
        "expiration",
        "successorEntity",
        "successorEntities",
        "eventGroups",
    }
    missing = sorted(required_entity_fields - set(entity))
    if missing:
        raise ValueError(f"{context}.attributes.entity is missing fields: {missing!r}")

    successor_entity = _optional_object(
        entity["successorEntity"], f"{context}.attributes.entity.successorEntity"
    )
    if successor_entity is not None:
        _validate_successor(
            successor_entity, f"{context}.attributes.entity.successorEntity"
        )
    successor_entities = _object_tuple(
        entity["successorEntities"],
        f"{context}.attributes.entity.successorEntities",
    )
    for successor_index, successor in enumerate(successor_entities):
        _validate_successor(
            successor,
            f"{context}.attributes.entity.successorEntities[{successor_index}]",
        )

    creation_date = _optional_timestamp(
        entity.get("creationDate"), f"{context}.attributes.entity.creationDate"
    )
    registration_authority = _parse_authority(
        entity["registeredAt"], f"{context}.attributes.entity.registeredAt"
    )

    return GLEIFLEIRecord(
        lei=resource_lei,
        legal_name=_parse_name(
            entity["legalName"],
            f"{context}.attributes.entity.legalName",
            require_language=False,
            require_type=False,
        ),
        other_names=_parse_names(
            entity["otherNames"], f"{context}.attributes.entity.otherNames"
        ),
        transliterated_other_names=_parse_names(
            entity["transliteratedOtherNames"],
            f"{context}.attributes.entity.transliteratedOtherNames",
        ),
        legal_address=_parse_address(
            entity["legalAddress"], f"{context}.attributes.entity.legalAddress"
        ),
        headquarters_address=_parse_address(
            entity["headquartersAddress"],
            f"{context}.attributes.entity.headquartersAddress",
        ),
        jurisdiction=_optional_text(
            entity["jurisdiction"], f"{context}.attributes.entity.jurisdiction"
        ),
        category=_optional_text(
            entity["category"], f"{context}.attributes.entity.category"
        ),
        sub_category=_optional_text(
            entity.get("subCategory"), f"{context}.attributes.entity.subCategory"
        ),
        status=_required_text(
            entity["status"], f"{context}.attributes.entity.status"
        ),
        registration_authority=registration_authority,
        registration_entity_id=_optional_text(
            entity["registeredAs"], f"{context}.attributes.entity.registeredAs"
        ),
        legal_form=_optional_object(
            entity.get("legalForm"), f"{context}.attributes.entity.legalForm"
        ),
        creation_date=creation_date,
        expiration=_optional_object(
            entity["expiration"], f"{context}.attributes.entity.expiration"
        ),
        successor_entity=successor_entity,
        successor_entities=successor_entities,
        event_groups=_object_tuple(
            entity["eventGroups"], f"{context}.attributes.entity.eventGroups"
        ),
        registration=_parse_registration(
            attributes.get("registration"), f"{context}.attributes.registration"
        ),
    )


def parse_lei_jsonapi_bytes(raw: bytes) -> GLEIFLEISnapshot:
    """Parse one archived GLEIF Level 1 JSON:API response without I/O."""

    if not isinstance(raw, bytes):
        raise TypeError("GLEIF Level 1 input must be bytes")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError("GLEIF Level 1 input must be valid UTF-8") from error
    try:
        payload = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except _DuplicateJSONKey as error:
        raise ValueError(f"GLEIF Level 1 input contains {error}") from error
    except json.JSONDecodeError as error:
        raise ValueError("GLEIF Level 1 input is not valid JSON") from error

    document = _object(payload, "GLEIF Level 1 response")
    meta = _object(document.get("meta"), "meta")
    golden_copy = _object(meta.get("goldenCopy"), "meta.goldenCopy")
    if set(golden_copy) != {"publishDate"}:
        raise ValueError("meta.goldenCopy must contain exactly one publishDate")
    publish_date = _timestamp(
        golden_copy["publishDate"], "meta.goldenCopy.publishDate"
    )

    data = document.get("data")
    resources = data if isinstance(data, list) else [data]
    if data is None or not all(isinstance(resource, dict) for resource in resources):
        raise ValueError("data must be a resource object or an array of resource objects")
    records = tuple(
        _parse_record(resource, index) for index, resource in enumerate(resources)
    )
    leis = [record.lei for record in records]
    if len(leis) != len(set(leis)):
        raise ValueError("GLEIF Level 1 response contains duplicate LEIs")
    if leis != sorted(leis):
        raise ValueError("GLEIF Level 1 response records must be sorted by LEI")

    return GLEIFLEISnapshot(
        golden_copy_publish_date=publish_date,
        records=records,
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        raw_bytes=len(raw),
    )


def _name_payload(name: GLEIFName) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": name.name, "language": name.language}
    if name.name_type is not None:
        payload["type"] = name.name_type
    return payload


def _address_payload(address: GLEIFAddress) -> dict[str, Any]:
    return {
        "language": address.language,
        "addressLines": list(address.address_lines),
        "addressNumber": address.address_number,
        "addressNumberWithinBuilding": address.address_number_within_building,
        "mailRouting": address.mail_routing,
        "city": address.city,
        "region": address.region,
        "country": address.country,
        "postalCode": address.postal_code,
    }


def _authority_payload(authority: GLEIFAuthority | None) -> dict[str, Any] | None:
    if authority is None:
        return None
    return {"id": authority.authority_id, "other": authority.other}


def _registration_payload(registration: GLEIFRegistration) -> dict[str, Any]:
    return {
        "initialRegistrationDate": registration.initial_registration_date,
        "lastUpdateDate": registration.last_update_date,
        "status": registration.status,
        "nextRenewalDate": registration.next_renewal_date,
        "managingLou": registration.managing_lou,
        "corroborationLevel": registration.corroboration_level,
        "validatedAt": _authority_payload(registration.validated_at),
        "validatedAs": registration.validated_as,
        "otherValidationAuthorities": list(
            registration.other_validation_authorities
        ),
    }


def _record_payload(record: GLEIFLEIRecord) -> dict[str, Any]:
    entity = {
        "legalName": _name_payload(record.legal_name),
        "otherNames": [_name_payload(name) for name in record.other_names],
        "transliteratedOtherNames": [
            _name_payload(name) for name in record.transliterated_other_names
        ],
        "legalAddress": _address_payload(record.legal_address),
        "headquartersAddress": _address_payload(record.headquarters_address),
        "registeredAt": _authority_payload(record.registration_authority),
        "registeredAs": record.registration_entity_id,
        "jurisdiction": record.jurisdiction,
        "category": record.category,
        "subCategory": record.sub_category,
        "legalForm": record.legal_form,
        "status": record.status,
        "creationDate": record.creation_date,
        "expiration": record.expiration,
        "successorEntity": record.successor_entity,
        "successorEntities": list(record.successor_entities),
        "eventGroups": list(record.event_groups),
    }
    return {
        "type": GLEIF_LEI_RESOURCE_TYPE,
        "id": record.lei,
        "attributes": {
            "lei": record.lei,
            "entity": entity,
            "registration": _registration_payload(record.registration),
        },
    }


def canonical_lei_payload_bytes(snapshot: GLEIFLEISnapshot) -> bytes:
    """Return deterministic, link-free JSON:API bytes for a parsed snapshot."""

    if not isinstance(snapshot, GLEIFLEISnapshot):
        raise TypeError("snapshot must be a GLEIFLEISnapshot")
    payload = {
        "format": GLEIF_LEI_CANONICAL_FORMAT,
        "meta": {
            "goldenCopy": {"publishDate": snapshot.golden_copy_publish_date}
        },
        "data": [_record_payload(record) for record in snapshot.records],
    }
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def canonical_lei_payload_sha256(snapshot: GLEIFLEISnapshot) -> str:
    return hashlib.sha256(canonical_lei_payload_bytes(snapshot)).hexdigest()


parse_gleif_lei_jsonapi_bytes = parse_lei_jsonapi_bytes


__all__ = [
    "GLEIFAddress",
    "GLEIFAuthority",
    "GLEIFLEIRecord",
    "GLEIFLEISnapshot",
    "GLEIFName",
    "GLEIFRegistration",
    "GLEIF_LEI_CANONICAL_FORMAT",
    "GLEIF_LEI_RESOURCE_TYPE",
    "canonical_lei_payload_bytes",
    "canonical_lei_payload_sha256",
    "parse_gleif_lei_jsonapi_bytes",
    "parse_lei_jsonapi_bytes",
]
