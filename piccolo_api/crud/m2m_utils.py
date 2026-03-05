import uuid
from typing import Any, TypeGuard, Union

from piccolo.columns.m2m import M2M
from piccolo.columns.reverse_lookup import ReverseLookup
from piccolo.table import Table


# helper to filter only m2m type not reverse_lookup
def is_m2m(item: Union[M2M, ReverseLookup]) -> TypeGuard[M2M]:
    return isinstance(item, M2M)


async def reverse_m2m_lookup(table: type[Table], rows: Any) -> None:
    # collect primary keys from incoming rows
    try:
        primary_key_values = [row[table._meta.primary_key] for row in rows]
    except Exception:
        return

    # bulk fetch primary objects using subquery
    primary_table_objects: list[Any] = []
    try:
        primary_table_objects = await table.objects().where(
            table._meta.primary_key.is_in(primary_key_values)
        )
    except Exception:
        primary_table_objects = []
        for row in rows:
            try:
                primary_table_objects.append(
                    await table.objects().get(
                        table._meta.primary_key == row[table._meta.primary_key]
                    )
                )
            except Exception:
                continue

    # primary key map for quick lookup
    primary_table_object_map = {
        primary_table_object._meta.primary_key: primary_table_object
        for primary_table_object in primary_table_objects
    }

    for row in rows:
        primary_table_object = primary_table_object_map.get(
            row.get(table._meta.primary_key)
        )

        for _, item in enumerate(table._meta.m2m_relationships):
            if not is_m2m(item):
                continue

            secondary_table_m2m_column_name = item._meta._name

            if not primary_table_object:
                row[secondary_table_m2m_column_name] = []
                continue

            try:
                secondary_table_object = await primary_table_object.get_m2m(
                    item
                )
                row[secondary_table_m2m_column_name] = [
                    i.to_dict() for i in secondary_table_object
                ]
            except Exception:
                row[secondary_table_m2m_column_name] = []


async def reverse_m2m_lookup_single_row(
    table: type[Table],
    row: Any,
    row_id: Union[str, uuid.UUID, int],
) -> None:
    try:
        primary_table_object: Any = await table.objects().get(
            table._meta.primary_key == row_id
        )
    except Exception:
        for _, item in enumerate(table._meta.m2m_relationships):
            row[item._meta._name] = []
        return

    for _, item in enumerate(table._meta.m2m_relationships):
        if not is_m2m(item):
            continue

        secondary_table_m2m_column_name = item._meta._name
        secondary_table = item._meta.secondary_table
        secondary_table_readable = (
            secondary_table.get_readable().columns[0]._meta.name
        )

        try:
            secondary_table_object = await primary_table_object.get_m2m(item)
            row[secondary_table_m2m_column_name] = [
                i.to_dict().get(secondary_table_readable)
                for i in secondary_table_object
            ]
        except Exception:
            row[secondary_table_m2m_column_name] = []


async def create_m2m(
    table: type[Table],
    data: dict[str, Any],
    row: Any,
    cleaned_data: Any,
    response_id: Union[str, uuid.UUID, int],
) -> None:
    results: list[Any] = []
    for index, item in enumerate(table._meta.m2m_relationships):
        if not is_m2m(item):
            continue

        m2m_column_name = item._meta._name

        secondary_table = item._meta.secondary_table
        secondary_table_readable = secondary_table.get_readable().columns[0]

        row[m2m_column_name] = cleaned_data.get(m2m_column_name, []) or []

        # optimized bulk fetch secondary objects
        secondary_objects = []
        if row[m2m_column_name]:
            try:
                secondary_objects = await secondary_table.objects().where(
                    secondary_table_readable.is_in(row[m2m_column_name])
                )
            except Exception:
                secondary_objects = []
                for i in row[m2m_column_name]:
                    try:
                        data_value: Any = await secondary_table.objects().get(
                            secondary_table_readable == i
                        )
                        secondary_objects.append(data_value)
                    except Exception:
                        continue

        # preserve the original "results" structure
        results.append(secondary_objects)

    # save M2M relations
    for index, result in enumerate(results):
        for item in result:
            await row.add_m2m(
                item,
                m2m=table._meta.m2m_relationships[index],
            )


async def update_m2m(
    table: type[Table],
    cleaned_data: Any,
    row_id: Union[str, uuid.UUID, int],
) -> Any:
    obj: Any = await table.objects().get(table._meta.primary_key == row_id)

    m2m_column_names = [i._meta._name for i in table._meta.m2m_relationships]
    for key, value in cleaned_data.items():
        if key in m2m_column_names:
            continue
        setattr(obj, key, value)

    await obj.save()
    new_row = obj.to_dict()

    update_results: list[Any] = []

    for index, item in enumerate(table._meta.m2m_relationships):
        if not is_m2m(item):
            continue

        m2m_column_name = item._meta._name
        new_row[m2m_column_name] = cleaned_data.get(m2m_column_name, []) or []

        secondary_table = item._meta.secondary_table
        secondary_table_readable = secondary_table.get_readable().columns[0]

        # optimized bulk fetch desired secondary objects:
        desired_secondary_objects = []
        if new_row[m2m_column_name]:
            try:
                desired_secondary_objects = (
                    await secondary_table.objects().where(
                        secondary_table_readable.is_in(
                            new_row[m2m_column_name]
                        )
                    )
                )
            except Exception:
                desired_secondary_objects = []
                for i in new_row[m2m_column_name]:
                    try:
                        data_value: Any = await secondary_table.objects().get(
                            secondary_table_readable == i
                        )
                        desired_secondary_objects.append(data_value)
                    except Exception:
                        continue

        update_results.append(desired_secondary_objects)

        # fetch current related objects
        try:
            current_secondary_objects = await obj.get_m2m(item)
        except Exception:
            current_secondary_objects = []

        # compare by readable value (admin UI use that)
        try:
            desired_set = {
                item.to_dict().get(secondary_table_readable._meta.name)
                for item in desired_secondary_objects
            }
            current_set = {
                item.to_dict().get(secondary_table_readable._meta.name)
                for item in current_secondary_objects
            }
        except Exception:
            desired_set = set()
            current_set = set()

        # compare add/remove set values
        to_add = desired_set - current_set
        to_remove = current_set - desired_set

        # add m2m value
        if to_add:
            for item in desired_secondary_objects:  # type:ignore
                if (
                    item.to_dict().get(  # type:ignore
                        secondary_table_readable._meta.name
                    )
                    in to_add
                ):
                    await obj.add_m2m(
                        item,
                        m2m=table._meta.m2m_relationships[index],
                    )

        # remove m2m value
        if to_remove:
            remove_m2m_value = getattr(obj, "remove_m2m", None)
            assert remove_m2m_value
            for item in current_secondary_objects:
                if (
                    item.to_dict().get(  # type:ignore
                        secondary_table_readable._meta.name
                    )
                    in to_remove
                ):
                    await remove_m2m_value(
                        item,
                        m2m=table._meta.m2m_relationships[index],
                    )
    return new_row
