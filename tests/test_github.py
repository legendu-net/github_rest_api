from github_rest_api.github import User, Organization, Repository


def test_user_get_repositories():
    token = ""
    username = "dclong"
    user = User(token, username)
    repos = user.get_repositories()
    assert len(repos) > 0


def test_organization_get_repositories():
    token = ""
    org_name = "legendu-net"
    org = Organization(token, org_name)
    repos = org.get_repositories()
    assert len(repos) > 0


def test_repository_get_branch():
    token = ""
    repo_name = "dclong/github_rest_api"
    repo = Repository(token, repo_name)
    branch = repo.get_branch("main")
    assert branch["name"] == "main"
