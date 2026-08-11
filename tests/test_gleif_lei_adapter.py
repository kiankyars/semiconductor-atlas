from __future__ import annotations

import copy
import hashlib
import json
import unittest

from semiconductor_atlas.adapters.gleif_lei import (
    GLEIF_LEI_CANONICAL_FORMAT,
    GLEIF_LEI_RESOURCE_TYPE,
    canonical_lei_payload_bytes,
    canonical_lei_payload_sha256,
    parse_gleif_lei_jsonapi_bytes,
    parse_lei_jsonapi_bytes,
)


FIRST_LEI = "2549005GOBWLCSY63Q97"
SECOND_LEI = "KNX4USFCNGPY45LOCE31"
ANNULLED_LEI = "529900Z9HRA5529A5V36"


def _address(*, country: str = "US", city: str = "Tempe") -> dict[str, object]:
    return {
        "language": "en",
        "addressLines": ["7955 S Priest Dr.", "Suite 102"],
        "addressNumber": "7955",
        "addressNumberWithinBuilding": "102",
        "mailRouting": None,
        "city": city,
        "region": "US-AZ",
        "country": country,
        "postalCode": "85284",
    }


def _record(
    lei: str,
    name: str,
    *,
    successor_lei: str | None = None,
) -> dict[str, object]:
    return {
        "type": "lei-records",
        "id": lei,
        "attributes": {
            "lei": lei,
            "entity": {
                "legalName": {"name": name, "language": "en"},
                "otherNames": [
                    {
                        "name": f"{name} Trading",
                        "language": None,
                        "type": "TRADING_OR_OPERATING_NAME",
                    },
                    {
                        "name": f"Old {name}",
                        "language": "en",
                        "type": "PREVIOUS_LEGAL_NAME",
                    },
                ],
                "transliteratedOtherNames": [
                    {
                        "name": f"{name} ASCII",
                        "language": "en",
                        "type": "PREFERRED_ASCII_TRANSLITERATED_LEGAL_NAME",
                    }
                ],
                "legalAddress": _address(),
                "headquartersAddress": _address(city="Phoenix"),
                "registeredAt": {"id": "RA000602", "other": None},
                "registeredAs": "23456789",
                "jurisdiction": "US-AZ",
                "category": "GENERAL",
                "subCategory": None,
                "legalForm": {"id": "XTIQ", "other": None},
                "status": "ACTIVE",
                "creationDate": "2020-11-10T00:00:00Z",
                "expiration": {"date": None, "reason": None},
                "successorEntity": {"lei": None, "name": None},
                "successorEntities": (
                    [
                        {
                            "lei": successor_lei,
                            "name": "Successor Legal Entity",
                            "language": "en",
                        }
                    ]
                    if successor_lei
                    else []
                ),
                "eventGroups": [
                    {
                        "groupType": "STANDALONE",
                        "events": [
                            {
                                "validationDocuments": "SUPPORTING_DOCUMENTS",
                                "validationReference": "registry filing 123",
                                "effectiveDate": "2024-01-02T00:00:00Z",
                                "recordedDate": "2024-01-03T00:00:00Z",
                                "type": "CHANGE_LEGAL_NAME",
                                "status": "COMPLETED",
                            }
                        ],
                    }
                ],
            },
            "registration": {
                "initialRegistrationDate": "2021-01-01T00:00:00Z",
                "lastUpdateDate": "2026-07-01T12:30:00+00:00",
                "status": "ISSUED",
                "nextRenewalDate": "2027-07-01T00:00:00Z",
                "managingLou": lei,
                "corroborationLevel": "FULLY_CORROBORATED",
                "validatedAt": {"id": "RA000602", "other": None},
                "validatedAs": "23456789",
                "otherValidationAuthorities": [
                    {
                        "validatedAt": {"id": "RA000003", "other": None},
                        "validatedAs": "foreign-42",
                    }
                ],
            },
        },
        "relationships": {
            "direct-parent": {
                "links": {
                    "related": "https://api.gleif.org/this-link-is-not-followed"
                }
            }
        },
        "links": {"self": f"https://api.gleif.org/api/v1/lei-records/{lei}"},
    }


def _payload() -> dict[str, object]:
    return {
        "meta": {
            "goldenCopy": {"publishDate": "2026-07-19T16:00:00Z"},
            "pagination": {
                "currentPage": 1,
                "perPage": 2,
                "from": 1,
                "to": 2,
                "total": 2,
                "lastPage": 1,
            },
        },
        "links": {"first": "https://api.gleif.org/api/v1/lei-records"},
        "data": [
            _record(FIRST_LEI, "TSMC Arizona Corporation", successor_lei=SECOND_LEI),
            _record(SECOND_LEI, "INTEL CORPORATION"),
        ],
    }


def _annulled_null_heavy_record() -> dict[str, object]:
    """Current API shape for an old ANNULLED record with optional nulls."""

    record = _record(ANNULLED_LEI, "MEAG EuroRent I")
    entity = record["attributes"]["entity"]
    entity["legalName"]["language"] = None
    entity["otherNames"] = []
    entity["transliteratedOtherNames"] = []
    entity["legalAddress"] = {
        "language": None,
        "addressLines": ["Oskar-von-Miller-Ring 18"],
        "addressNumber": None,
        "addressNumberWithinBuilding": None,
        "mailRouting": "c/o MEAG MUNICH ERGO Kapitalanlagegesellschaft mbH",
        "city": "München",
        "region": "DE-BY",
        "country": "DE",
        "postalCode": "80333",
    }
    entity["headquartersAddress"] = copy.deepcopy(entity["legalAddress"])
    entity["registeredAt"] = {"id": "RA000373", "other": None}
    entity["registeredAs"] = None
    entity["jurisdiction"] = "DE"
    entity["category"] = "FUND"
    entity["subCategory"] = None
    entity["legalForm"] = {
        "id": "8888",
        "other": "Sondervermögen nach deutschem Recht (KAGB)",
    }
    entity["status"] = "NULL"
    entity["creationDate"] = None
    entity["expiration"] = {"date": None, "reason": None}
    entity["successorEntity"] = {"lei": None, "name": None}
    entity["successorEntities"] = []
    entity["eventGroups"] = []

    registration = record["attributes"]["registration"]
    registration.update(
        {
            "initialRegistrationDate": "2013-06-11T12:42:23Z",
            "lastUpdateDate": "2017-01-26T10:11:41Z",
            "status": "ANNULLED",
            "nextRenewalDate": "2014-06-11T12:42:23Z",
            "managingLou": "5299000J2N45DDNE4Y28",
            "corroborationLevel": "FULLY_CORROBORATED",
            "validatedAt": {"id": "RA000373", "other": None},
            "validatedAs": None,
            "otherValidationAuthorities": [],
        }
    )
    return record


def _bytes(payload: object, *, sort_keys: bool = False) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=sort_keys,
    ).encode("utf-8")


class GLEIFLEIAdapterTests(unittest.TestCase):
    def test_parses_and_preserves_bounded_level_one_fields(self) -> None:
        raw = _bytes(_payload())
        snapshot = parse_lei_jsonapi_bytes(raw)

        self.assertEqual("2026-07-19T16:00:00Z", snapshot.golden_copy_publish_date)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), snapshot.raw_sha256)
        self.assertEqual(len(raw), snapshot.raw_bytes)
        self.assertEqual((FIRST_LEI, SECOND_LEI), tuple(r.lei for r in snapshot.records))

        first = snapshot.records[0]
        self.assertEqual("TSMC Arizona Corporation", first.legal_name.name)
        self.assertEqual("en", first.legal_name.language)
        self.assertEqual(
            ("TRADING_OR_OPERATING_NAME", "PREVIOUS_LEGAL_NAME"),
            tuple(name.name_type for name in first.other_names),
        )
        self.assertIsNone(first.other_names[0].language)
        self.assertEqual(
            "PREFERRED_ASCII_TRANSLITERATED_LEGAL_NAME",
            first.transliterated_other_names[0].name_type,
        )
        self.assertEqual(("7955 S Priest Dr.", "Suite 102"), first.legal_address.address_lines)
        self.assertEqual("Phoenix", first.headquarters_address.city)
        self.assertEqual("US-AZ", first.jurisdiction)
        self.assertEqual("GENERAL", first.category)
        self.assertEqual("ACTIVE", first.status)
        self.assertEqual("RA000602", first.registration_authority.authority_id)
        self.assertEqual("23456789", first.registration_entity_id)
        self.assertEqual("ISSUED", first.registration.status)
        self.assertEqual(FIRST_LEI, first.registration.managing_lou)
        self.assertEqual(SECOND_LEI, first.successor_entities[0]["lei"])
        self.assertEqual(
            "CHANGE_LEGAL_NAME",
            first.event_groups[0]["events"][0]["type"],
        )
        self.assertFalse(hasattr(first, "relationships"))

    def test_accepts_official_single_resource_response_shape(self) -> None:
        payload = _payload()
        payload["data"] = payload["data"][0]
        snapshot = parse_gleif_lei_jsonapi_bytes(_bytes(payload))
        self.assertEqual((FIRST_LEI,), tuple(record.lei for record in snapshot.records))

    def test_accepts_current_annulled_record_with_optional_nulls(self) -> None:
        payload = {
            "meta": {
                "goldenCopy": {"publishDate": "2026-07-19T16:00:00Z"}
            },
            "data": _annulled_null_heavy_record(),
        }

        record = parse_lei_jsonapi_bytes(_bytes(payload)).records[0]

        self.assertEqual(ANNULLED_LEI, record.lei)
        self.assertIsNone(record.legal_name.language)
        self.assertIsNone(record.legal_address.language)
        self.assertIsNone(record.headquarters_address.language)
        self.assertIsNone(record.registration_entity_id)
        self.assertEqual("ANNULLED", record.registration.status)
        self.assertIsNone(record.registration.validated_as)
        self.assertEqual(
            "2014-06-11T12:42:23Z", record.registration.next_renewal_date
        )
        self.assertEqual("5299000J2N45DDNE4Y28", record.registration.managing_lou)
        self.assertEqual({"date": None, "reason": None}, record.expiration)
        self.assertEqual(
            {"lei": None, "name": None}, record.successor_entity
        )
        self.assertEqual((), record.successor_entities)

    def test_canonical_payload_is_deterministic_link_free_and_round_trips(self) -> None:
        payload = _payload()
        first = parse_lei_jsonapi_bytes(_bytes(payload))
        reordered = parse_lei_jsonapi_bytes(_bytes(payload, sort_keys=True))

        canonical = canonical_lei_payload_bytes(first)
        self.assertEqual(canonical, canonical_lei_payload_bytes(reordered))
        self.assertEqual(
            hashlib.sha256(canonical).hexdigest(),
            canonical_lei_payload_sha256(first),
        )
        self.assertTrue(canonical.endswith(b"\n"))
        decoded = json.loads(canonical)
        self.assertEqual(GLEIF_LEI_CANONICAL_FORMAT, decoded["format"])
        self.assertNotIn("relationships", decoded["data"][0])
        self.assertNotIn("links", decoded["data"][0])

        round_trip = parse_lei_jsonapi_bytes(canonical)
        self.assertEqual(first.golden_copy_publish_date, round_trip.golden_copy_publish_date)
        self.assertEqual(first.records, round_trip.records)

    def test_rejects_non_bytes_invalid_utf8_invalid_json_and_json_extensions(self) -> None:
        with self.assertRaisesRegex(TypeError, "must be bytes"):
            parse_lei_jsonapi_bytes("{}")  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "valid UTF-8"):
            parse_lei_jsonapi_bytes(b"\xff")
        with self.assertRaisesRegex(ValueError, "not valid JSON"):
            parse_lei_jsonapi_bytes(b"{")
        with self.assertRaisesRegex(ValueError, "non-standard JSON constant"):
            parse_lei_jsonapi_bytes(
                b'{"meta":{"goldenCopy":{"publishDate":NaN}},"data":[]}'
            )

    def test_rejects_duplicate_json_object_keys(self) -> None:
        raw = (
            b'{"meta":{"goldenCopy":{"publishDate":"2026-07-19T16:00:00Z"}},'
            b'"data":[],"data":[]}'
        )
        with self.assertRaisesRegex(ValueError, "duplicate JSON object key 'data'"):
            parse_lei_jsonapi_bytes(raw)

    def test_requires_one_timezone_aware_golden_copy_publish_date(self) -> None:
        cases = {
            "missing goldenCopy": {"meta": {}, "data": []},
            "missing publishDate": {"meta": {"goldenCopy": {}}, "data": []},
            "extra goldenCopy field": {
                "meta": {
                    "goldenCopy": {
                        "publishDate": "2026-07-19T16:00:00Z",
                        "other": "unexpected",
                    }
                },
                "data": [],
            },
            "naive timestamp": {
                "meta": {
                    "goldenCopy": {"publishDate": "2026-07-19T16:00:00"}
                },
                "data": [],
            },
        }
        for label, payload in cases.items():
            with self.subTest(label=label), self.assertRaises(ValueError):
                parse_lei_jsonapi_bytes(_bytes(payload))

    def test_requires_lei_record_type_format_and_matching_attribute(self) -> None:
        mutations = []
        wrong_type = _payload()
        wrong_type["data"][0]["type"] = "relationship-records"
        mutations.append(wrong_type)
        malformed_id = _payload()
        malformed_id["data"][0]["id"] = "lowercase-not-an-lei"
        mutations.append(malformed_id)
        mismatch = _payload()
        mismatch["data"][0]["attributes"]["lei"] = SECOND_LEI
        mutations.append(mismatch)

        for payload in mutations:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                parse_lei_jsonapi_bytes(_bytes(payload))
        self.assertEqual("lei-records", GLEIF_LEI_RESOURCE_TYPE)

    def test_requires_unique_sorted_leis(self) -> None:
        unsorted = _payload()
        unsorted["data"].reverse()
        with self.assertRaisesRegex(ValueError, "sorted by LEI"):
            parse_lei_jsonapi_bytes(_bytes(unsorted))

        duplicate = _payload()
        duplicate["data"][1] = copy.deepcopy(duplicate["data"][0])
        with self.assertRaisesRegex(ValueError, "duplicate LEIs"):
            parse_lei_jsonapi_bytes(_bytes(duplicate))

    def test_rejects_malformed_legal_name_and_aliases(self) -> None:
        malformed_name = _payload()
        del malformed_name["data"][0]["attributes"]["entity"]["legalName"]["language"]
        with self.assertRaisesRegex(ValueError, "invalid name structure"):
            parse_lei_jsonapi_bytes(_bytes(malformed_name))

        malformed_alias = _payload()
        malformed_alias["data"][0]["attributes"]["entity"]["otherNames"][0]["type"] = None
        with self.assertRaisesRegex(ValueError, "must be a non-empty clean string"):
            parse_lei_jsonapi_bytes(_bytes(malformed_alias))

    def test_rejects_malformed_addresses_authorities_and_registration(self) -> None:
        cases = []
        bad_address = _payload()
        bad_address["data"][0]["attributes"]["entity"]["legalAddress"]["country"] = "usa"
        cases.append(bad_address)
        empty_address = _payload()
        empty_address["data"][0]["attributes"]["entity"]["legalAddress"]["addressLines"] = []
        cases.append(empty_address)
        missing_city = _payload()
        missing_city["data"][0]["attributes"]["entity"]["legalAddress"]["city"] = None
        cases.append(missing_city)
        bad_authority = _payload()
        bad_authority["data"][0]["attributes"]["entity"]["registeredAt"]["id"] = "registry"
        cases.append(bad_authority)
        naive_registration_clock = _payload()
        naive_registration_clock["data"][0]["attributes"]["registration"]["lastUpdateDate"] = "2026-07-01T12:30:00"
        cases.append(naive_registration_clock)
        bad_lou = _payload()
        bad_lou["data"][0]["attributes"]["registration"]["managingLou"] = "bad"
        cases.append(bad_lou)
        missing_next_renewal = _payload()
        missing_next_renewal["data"][0]["attributes"]["registration"]["nextRenewalDate"] = None
        cases.append(missing_next_renewal)

        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                parse_lei_jsonapi_bytes(_bytes(payload))

    def test_rejects_malformed_successor_and_event_containers(self) -> None:
        bad_successor = _payload()
        bad_successor["data"][0]["attributes"]["entity"]["successorEntities"][0]["lei"] = "bad"
        with self.assertRaisesRegex(ValueError, "uppercase alphanumeric LEI"):
            parse_lei_jsonapi_bytes(_bytes(bad_successor))

        bad_events = _payload()
        bad_events["data"][0]["attributes"]["entity"]["eventGroups"] = ["event"]
        with self.assertRaisesRegex(ValueError, "must be a JSON object"):
            parse_lei_jsonapi_bytes(_bytes(bad_events))

    def test_requires_the_full_entity_placeholder_shape(self) -> None:
        for field in (
            "registeredAt",
            "registeredAs",
            "expiration",
            "successorEntity",
            "successorEntities",
            "eventGroups",
        ):
            payload = _payload()
            del payload["data"][0]["attributes"]["entity"][field]
            with self.subTest(field=field), self.assertRaisesRegex(
                ValueError,
                "is missing fields",
            ):
                parse_lei_jsonapi_bytes(_bytes(payload))


if __name__ == "__main__":
    unittest.main()
