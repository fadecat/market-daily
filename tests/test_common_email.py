from src.common import email


def test_load_email_config_supports_custom_recipient_variable(monkeypatch):
    monkeypatch.setenv("SMTP_USER", "sender@example.com")
    monkeypatch.setenv("SMTP_PASS", "secret")
    monkeypatch.setenv("EMAIL_FROM", "sender@example.com")
    monkeypatch.setenv(
        "DIVIDEND_OBSERVATION_RECEIVER_EMAIL",
        "alpha@example.com,beta@example.com",
    )
    monkeypatch.delenv("RECEIVER_EMAIL", raising=False)
    monkeypatch.delenv("EMAIL_TO", raising=False)

    config = email.load_email_config(
        recipient_env_name="DIVIDEND_OBSERVATION_RECEIVER_EMAIL"
    )

    assert config["recipients"] == ["alpha@example.com", "beta@example.com"]
    assert config["sender"] == "sender@example.com"


def test_load_email_config_keeps_default_receiver_behavior(monkeypatch):
    monkeypatch.setenv("SMTP_USER", "sender@example.com")
    monkeypatch.setenv("SMTP_PASS", "secret")
    monkeypatch.setenv("EMAIL_FROM", "sender@example.com")
    monkeypatch.setenv("RECEIVER_EMAIL", "default@example.com")

    config = email.load_email_config()

    assert config["recipients"] == ["default@example.com"]
