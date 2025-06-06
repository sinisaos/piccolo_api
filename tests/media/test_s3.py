import asyncio
import io
import os
import uuid
from unittest import TestCase
from unittest.mock import MagicMock, patch

import boto3
from botocore import UNSIGNED
from botocore.config import Config
from moto import mock_aws
from piccolo.columns.column_types import Array, Varchar
from piccolo.table import Table

from piccolo_api.media.s3 import S3MediaStorage


class Movie(Table):
    poster = Varchar()
    screenshots = Array(base_column=Varchar())


class TestS3MediaStorage(TestCase):
    def setUp(self) -> None:
        Movie.create_table(if_not_exists=True).run_sync()

    def tearDown(self):
        Movie.alter().drop_table().run_sync()

    @patch("piccolo_api.media.base.uuid")
    @patch("piccolo_api.media.s3.S3MediaStorage.get_client")
    def test_store_file(self, get_client: MagicMock, uuid_module: MagicMock):
        """
        Make sure we can store files, and retrieve them.
        """
        uuid_module.uuid4.return_value = uuid.UUID(
            "fd0125c7-8777-4976-83c1-81605d5ab155"
        )
        bucket_name = "bucket123"
        folder_name = "movie_posters"

        with mock_aws():
            s3 = boto3.resource("s3", region_name="us-east-1")
            s3.create_bucket(Bucket=bucket_name)

            connection_kwargs = {
                "aws_access_key_id": "abc123",
                "aws_secret_access_key": "xyz123",
                "region_name": "us-east-1",
            }

            get_client.return_value = boto3.client("s3", **connection_kwargs)

            storage = S3MediaStorage(
                column=Movie.poster,
                bucket_name=bucket_name,
                folder_name=folder_name,
                connection_kwargs=connection_kwargs,
                upload_metadata={
                    "ACL": "public-read",
                    "Metadata": {"visibility": "premium"},
                    "CacheControl": "max-age=86400",
                },
            )

            with open(
                os.path.join(os.path.dirname(__file__), "test_files/bulb.jpg"),
                "rb",
            ) as test_file:
                # Store the file
                file_key = asyncio.run(
                    storage.store_file(file_name="bulb.jpg", file=test_file)
                )

                # Retrieve the URL for the file
                url = asyncio.run(
                    storage.generate_file_url(file_key, root_url="")
                )

                path, params = url.split("?", 1)

                self.assertEqual(
                    path,
                    f"https://{bucket_name}.s3.amazonaws.com/{folder_name}/{file_key}",  # noqa: E501
                )

                # We're parsing a string like this:
                # AWSAccessKeyId=abc123&Signature=abc123&Expires=1659437428
                params_list = [i.split("=") for i in params.split("&")]

                params_dict = {i[0]: i[1] for i in params_list}

                self.assertEqual(
                    params_dict["AWSAccessKeyId"],
                    connection_kwargs["aws_access_key_id"],
                )
                self.assertIn("Signature", params_dict)
                self.assertIn("Expires", params_dict)

                # Get the file
                file = asyncio.run(storage.get_file(file_key=file_key))
                assert file is not None
                self.assertEqual(
                    file.read(),
                    # We need to reopen the test file, in case it's closed:
                    open(test_file.name, "rb").read(),
                )

                # List file keys
                file_keys = asyncio.run(storage.get_file_keys())
                self.assertListEqual(file_keys, [file_key])

                # Delete the file
                asyncio.run(storage.delete_file(file_key=file_key))
                file_keys = asyncio.run(storage.get_file_keys())
                self.assertListEqual(file_keys, [])

                # Test bulk deletion
                file_keys = []
                for file_name in ("file_1.txt", "file_2.txt", "file_3.txt"):
                    file = io.BytesIO(b"test")
                    file_key = asyncio.run(
                        storage.store_file(file_name=file_name, file=file)
                    )
                    file_keys.append(file_key)

                asyncio.run(storage.bulk_delete_files(file_keys=file_keys[:2]))

                self.assertListEqual(
                    asyncio.run(storage.get_file_keys()), file_keys[2:]
                )

    @patch("piccolo_api.media.base.uuid")
    @patch("piccolo_api.media.s3.S3MediaStorage.get_client")
    def test_unsigned(self, get_client: MagicMock, uuid_module: MagicMock):
        """
        Make sure we can enable unsigned URLs if requested.
        """
        uuid_module.uuid4.return_value = uuid.UUID(
            "fd0125c7-8777-4976-83c1-81605d5ab155"
        )
        bucket_name = "bucket123"
        folder_name = "movie_posters"

        with mock_aws():
            s3 = boto3.resource("s3", region_name="us-east-1")
            s3.create_bucket(Bucket=bucket_name)

            connection_kwargs = {
                "aws_access_key_id": "abc123",
                "aws_secret_access_key": "xyz123",
                "region_name": "us-east-1",
            }

            get_client.return_value = boto3.client(
                "s3",
                **connection_kwargs,
                config=Config(signature_version=UNSIGNED),
            )

            storage = S3MediaStorage(
                column=Movie.poster,
                bucket_name=bucket_name,
                folder_name=folder_name,
                connection_kwargs=connection_kwargs,
                sign_urls=False,  # The important bit
                upload_metadata={
                    "ACL": "public-read",
                    "Metadata": {"visibility": "premium"},
                    "CacheControl": "max-age=86400",
                },
            )

            with open(
                os.path.join(os.path.dirname(__file__), "test_files/bulb.jpg"),
                "rb",
            ) as test_file:
                # Store the file
                file_key = asyncio.run(
                    storage.store_file(file_name="bulb.jpg", file=test_file)
                )

                # Retrieve the URL for the file
                url = asyncio.run(
                    storage.generate_file_url(file_key, root_url="")
                )

                # Make sure the correct config was passed to our mocked client.
                config = get_client.call_args[1].get("config")
                self.assertIs(config.signature_version, UNSIGNED)

                self.assertEqual(
                    url,
                    f"https://{bucket_name}.s3.amazonaws.com/{folder_name}/{file_key}",  # noqa: E501
                )

    @patch("piccolo_api.media.base.uuid")
    @patch("piccolo_api.media.s3.S3MediaStorage.get_client")
    def test_no_folder(self, get_client: MagicMock, uuid_module: MagicMock):
        """
        Make sure we can store files, and retrieve them when the
        ``folder_name`` is ``None``.
        """
        uuid_module.uuid4.return_value = uuid.UUID(
            "fd0125c7-8777-4976-83c1-81605d5ab155"
        )
        bucket_name = "bucket123"

        with mock_aws():
            s3 = boto3.resource("s3", region_name="us-east-1")
            s3.create_bucket(Bucket=bucket_name)

            connection_kwargs = {
                "aws_access_key_id": "abc123",
                "aws_secret_access_key": "xyz123",
                "region_name": "us-east-1",
            }

            get_client.return_value = boto3.client("s3", **connection_kwargs)

            storage = S3MediaStorage(
                column=Movie.poster,
                bucket_name=bucket_name,
                folder_name=None,
                connection_kwargs=connection_kwargs,
                upload_metadata={
                    "ACL": "public-read",
                    "Metadata": {"visibility": "premium"},
                    "CacheControl": "max-age=86400",
                },
            )

            with open(
                os.path.join(os.path.dirname(__file__), "test_files/bulb.jpg"),
                "rb",
            ) as test_file:
                # Store the file
                file_key = asyncio.run(
                    storage.store_file(file_name="bulb.jpg", file=test_file)
                )

                # Retrieve the URL for the file
                url = asyncio.run(
                    storage.generate_file_url(file_key, root_url="")
                )

                path, params = url.split("?", 1)

                self.assertEqual(
                    path,
                    f"https://{bucket_name}.s3.amazonaws.com/{file_key}",  # noqa: E501
                )

                # We're parsing a string like this:
                # AWSAccessKeyId=abc123&Signature=abc123&Expires=1659437428
                params_list = [i.split("=") for i in params.split("&")]

                params_dict = {i[0]: i[1] for i in params_list}

                self.assertEqual(
                    params_dict["AWSAccessKeyId"],
                    connection_kwargs["aws_access_key_id"],
                )
                self.assertIn("Signature", params_dict)
                self.assertIn("Expires", params_dict)

                # Get the file
                file = asyncio.run(storage.get_file(file_key=file_key))
                assert file is not None
                self.assertEqual(
                    file.read(),
                    # We need to reopen the test file, in case it's closed:
                    open(test_file.name, "rb").read(),
                )

                # List file keys
                file_keys = asyncio.run(storage.get_file_keys())
                self.assertListEqual(file_keys, [file_key])

                # Delete the file
                asyncio.run(storage.delete_file(file_key=file_key))
                file_keys = asyncio.run(storage.get_file_keys())
                self.assertListEqual(file_keys, [])

                # Test bulk deletion
                file_keys = []
                for file_name in ("file_1.txt", "file_2.txt", "file_3.txt"):
                    file = io.BytesIO(b"test")
                    file_key = asyncio.run(
                        storage.store_file(file_name=file_name, file=file)
                    )
                    file_keys.append(file_key)

                asyncio.run(storage.bulk_delete_files(file_keys=file_keys[:2]))

                self.assertListEqual(
                    asyncio.run(storage.get_file_keys()), file_keys[2:]
                )


class TestFolderName(TestCase):
    """
    Make sure the folder name is correctly added to the file key.
    """

    def test_with_folder_name(self):
        storage = S3MediaStorage(
            column=Movie.poster,
            bucket_name="test_bucket",
            folder_name="test_folder",
            connection_kwargs={},
        )
        self.assertEqual(
            storage._prepend_folder_name(file_key="abc123.jpeg"),
            "test_folder/abc123.jpeg",
        )

    def test_without_folder_name(self):
        storage = S3MediaStorage(
            column=Movie.poster,
            bucket_name="test_bucket",
            folder_name=None,
            connection_kwargs={},
        )
        self.assertEqual(
            storage._prepend_folder_name(file_key="abc123.jpeg"),
            "abc123.jpeg",
        )
