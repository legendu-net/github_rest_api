from github_rest_api.github import User, Organization


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
