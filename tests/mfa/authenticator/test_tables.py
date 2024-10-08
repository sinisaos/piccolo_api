import datetime
from unittest import TestCase
from unittest.mock import MagicMock, patch

import pyotp
from piccolo.apps.user.tables import BaseUser
from piccolo.testing.test_case import AsyncTableTest

from example_projects.mfa_demo.app import EXAMPLE_DB_ENCRYPTION_KEY
from piccolo_api.encryption.providers import XChaCha20Provider
from piccolo_api.mfa.authenticator.tables import AuthenticatorSecret


class TestGenerateSecret(TestCase):

    def test_generate_secret(self):
        """
        Make sure secrets are generated correctly.
        """
        secret_1 = AuthenticatorSecret.generate_secret()
        secret_2 = AuthenticatorSecret.generate_secret()

        self.assertIsInstance(secret_1, str)
        self.assertNotEqual(secret_1, secret_2)
        self.assertEqual(len(secret_1), 32)


class TestAuthenticate(AsyncTableTest):

    tables = [AuthenticatorSecret, BaseUser]

    @patch("piccolo_api.mfa.authenticator.tables.logger")
    async def test_replay_attack(self, logger: MagicMock):
        """
        If a token which was just used successfully is reused, it should be
        rejected, because it might be a replay attack.
        """
        user = await BaseUser.create_user(
            username="test", password="test123456"
        )

        code = "123456"

        secret, _ = await AuthenticatorSecret.create_new(
            user_id=user.id,
            encryption_provider=XChaCha20Provider(
                encryption_key=EXAMPLE_DB_ENCRYPTION_KEY
            ),
        )
        secret.last_used_code = code
        await secret.save()

        auth_response = await AuthenticatorSecret.authenticate(
            user_id=user.id,
            code=code,
            encryption_provider=XChaCha20Provider(
                encryption_key=EXAMPLE_DB_ENCRYPTION_KEY
            ),
        )
        assert auth_response is False

        logger.warning.assert_called_with(
            "User 1 reused a token - potential replay attack."
        )

    async def test_code(self):
        """
        Make sure a valid code can be used to authenticate.
        """
        user = await BaseUser.create_user(
            username="test", password="test123456"
        )

        encryption_provider = XChaCha20Provider(
            encryption_key=EXAMPLE_DB_ENCRYPTION_KEY
        )

        authenticator_secret, _ = await AuthenticatorSecret.create_new(
            user_id=user.id,
            encryption_provider=encryption_provider,
        )

        secret = encryption_provider.decrypt(authenticator_secret.secret)

        # Make sure a valid code works
        auth_response = await AuthenticatorSecret.authenticate(
            user_id=user.id,
            code=pyotp.TOTP(s=secret).now(),
            encryption_provider=encryption_provider,
        )
        assert auth_response is True

        # Make sure an invalid code fails
        auth_response = await AuthenticatorSecret.authenticate(
            user_id=user.id,
            code="ABC123",
            encryption_provider=encryption_provider,
        )
        assert auth_response is False

    async def test_recovery_code(self):
        """
        Make sure a valid recovery code can be used to authenticate.
        """
        user = await BaseUser.create_user(
            username="test", password="test123456"
        )

        encryption_provider = XChaCha20Provider(
            encryption_key=EXAMPLE_DB_ENCRYPTION_KEY
        )

        _, recovery_codes = await AuthenticatorSecret.create_new(
            user_id=user.id,
            encryption_provider=encryption_provider,
        )

        # Make sure a valid recovery code works
        auth_response = await AuthenticatorSecret.authenticate(
            user_id=user.id,
            code=recovery_codes[0],
            encryption_provider=encryption_provider,
        )
        assert auth_response is True

        # Make sure an invalid recovery code fails
        fake_code = "".join("a" for _ in range(len(recovery_codes[0])))
        auth_response = await AuthenticatorSecret.authenticate(
            user_id=user.id,
            code=fake_code,
            encryption_provider=encryption_provider,
        )
        assert auth_response is False

    async def test_unenrolled_user(self):
        """
        Make sure a user who isn't enrolled fails authentication.
        """
        user = await BaseUser.create_user(
            username="test", password="test123456"
        )

        auth_response = await AuthenticatorSecret.authenticate(
            user_id=user.id,
            code="abc123",
            encryption_provider=XChaCha20Provider(
                encryption_key=EXAMPLE_DB_ENCRYPTION_KEY
            ),
        )
        assert auth_response is False


class TestCreateNew(AsyncTableTest):

    tables = [AuthenticatorSecret, BaseUser]

    async def test_create_new(self):
        user = await BaseUser.create_user(
            username="test", password="test123456"
        )

        secret, _ = await AuthenticatorSecret.create_new(
            user_id=user.id,
            encryption_provider=XChaCha20Provider(
                encryption_key=EXAMPLE_DB_ENCRYPTION_KEY
            ),
        )

        self.assertEqual(secret.id, user.id)
        self.assertIsNotNone(secret.secret)
        self.assertIsInstance(secret.created_at, datetime.datetime)
        self.assertIsNone(secret.last_used_at)
        self.assertIsNone(secret.revoked_at)
        self.assertIsNone(secret.last_used_code)


class TestRevoke(AsyncTableTest):
    """
    Make sure we can revoke a user's MFA code.
    """

    tables = [AuthenticatorSecret, BaseUser]

    async def test_revoke(self):
        user = await BaseUser.create_user(
            username="test", password="test123456"
        )

        secret, _ = await AuthenticatorSecret.create_new(
            user_id=user.id,
            encryption_provider=XChaCha20Provider(
                encryption_key=EXAMPLE_DB_ENCRYPTION_KEY
            ),
        )

        await AuthenticatorSecret.revoke(user_id=user.id)

        await secret.refresh()

        assert secret.revoked_at is not None
