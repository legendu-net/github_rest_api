import os
from base64 import b64decode
from nacl import encoding, public
from github_rest_api.github import User, Organization, Repository, _encrypt_secret

TOKEN = os.environ.get("GITHUB_TOKEN", "")


def test_encrypt_secret_roundtrip():
    private_key = public.PrivateKey.generate()
    public_key = private_key.public_key.encode(encoding.Base64Encoder).decode()
    encrypted = _encrypt_secret(public_key, "s3cret-value")
    decrypted = public.SealedBox(private_key).decrypt(b64decode(encrypted))
    assert decrypted == b"s3cret-value"


def test_user_get_repositories():
    user = User(TOKEN, "dclong")
    repos = user.get_repositories()
    assert len(repos) > 0


def test_organization_get_repositories():
    org = Organization(TOKEN, "legendu-net")
    repos = org.get_repositories()
    assert len(repos) > 0


def test_repository_get_branch():
    repo = Repository(TOKEN, "legendu-net/github_rest_api")
    branch = repo.get_branch("main")
    assert branch["name"] == "main"
