def test_login_logout_flow(client):
    response = client.get("/dashboard")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]

    response = client.post("/login", data={"password": "wrong"}, follow_redirects=True)
    assert response.status_code == 200
    assert b"Invalid password." in response.data

    response = client.post("/login", data={"password": "secret123"})
    assert response.status_code == 302
    assert "/dashboard" in response.headers["Location"]

    response = client.post("/logout", follow_redirects=True)
    assert response.status_code == 200
    assert b"Logged out." in response.data
