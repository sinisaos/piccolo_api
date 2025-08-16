import uuid
from typing import Any, Union

from piccolo.table import Table


async def reverse_m2m_lookup(table: type[Table], rows: Any) -> None:
    try:
        for row in rows:
            for index, item in enumerate(table._meta.m2m_relationships):
                primary_table_object = await table.objects().where(
                    table._meta.primary_key == row[table._meta.primary_key]
                )
                secondary_table_m2m_column_name = (
                    table._meta.m2m_relationships[index]._meta._name
                )
                secondary_table_object = await primary_table_object[0].get_m2m(
                    table._meta.m2m_relationships[index]
                )
                # list of objects
                row[secondary_table_m2m_column_name] = [
                    i.to_dict() for i in secondary_table_object
                ]
    except ValueError:
        row[secondary_table_m2m_column_name] = []


async def reverse_m2m_lookup_single_row(
    table: type[Table],
    row: Any,
    row_id: Union[str, uuid.UUID, int],
) -> None:
    try:
        for index, item in enumerate(table._meta.m2m_relationships):
            primary_table_object = await table.objects().where(
                table._meta.primary_key == row_id
            )
            secondary_table_m2m_column_name = table._meta.m2m_relationships[
                index
            ]._meta._name
            secondary_table = table._meta.m2m_relationships[
                index
            ]._meta.secondary_table
            secondary_table_readable = (
                secondary_table.get_readable().columns[0]._meta.name
            )
            secondary_table_object = await primary_table_object[0].get_m2m(
                table._meta.m2m_relationships[index]
            )
            row[secondary_table_m2m_column_name] = [
                i.to_dict()[secondary_table_readable]
                for i in secondary_table_object
            ]
    except ValueError:
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
        m2m_column_name = [
            i._meta._name for i in table._meta.m2m_relationships
        ][index]
        secondary_table = table._meta.m2m_relationships[
            index
        ]._meta.secondary_table
        secondary_table_readable = secondary_table.get_readable().columns[0]
        # add m2m column result to new object
        row[m2m_column_name] = cleaned_data[m2m_column_name]
        # work out m2m relations in form
        secondary_table = table._meta.m2m_relationships[
            index
        ]._meta.secondary_table
        secondary_table_readable = secondary_table.get_readable().columns[0]
        # secondary table objects
        secondary_objects = [
            await secondary_table.objects().get(secondary_table_readable == i)
            for i in row[m2m_column_name]
        ]
        # we need to do this if we have multiple m2m relations
        # per primary table
        results.append(secondary_objects)
    # save multiple m2m relation
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
    update_results: list[Any] = []
    for index, item in enumerate(table._meta.m2m_relationships):
        m2m_column_name = [
            i._meta._name for i in table._meta.m2m_relationships
        ][index]

        obj: Any = await table.objects().get(table._meta.primary_key == row_id)

        for key, value in cleaned_data.items():
            setattr(obj, key, value)

        await obj.save()
        new_row = obj.to_dict()
        # removed the row from the table to prevent
        # duplicate entries in the m2m join table.
        await table.delete().where(table._meta.primary_key == row_id)
        # create completely new primary table object
        new_primary_object = await table.objects().create(**new_row)
        # add m2m column result to new object
        new_row[m2m_column_name] = cleaned_data[m2m_column_name]
        # work out m2m relations in form
        secondary_table = table._meta.m2m_relationships[
            index
        ]._meta.secondary_table
        secondary_table_readable = secondary_table.get_readable().columns[0]
        # secondary table objects
        secondary_objects = [
            await secondary_table.objects().get(secondary_table_readable == i)
            for i in new_row[m2m_column_name]
        ]
        # we need to do this if we have multiple m2m relations
        # per primary table
        update_results.append(secondary_objects)
    # save multiple m2m relation
    for index, result in enumerate(update_results):
        for item in result:
            await new_primary_object.add_m2m(
                item,  # type: ignore
                m2m=table._meta.m2m_relationships[index],
            )
    return new_row
