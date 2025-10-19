# Convenience targets for running the bot

.PHONY: prod prod-ephemeral test-bot

prod:
	@echo "Running in production mode with persistent DB..."
	@LOG_LEVEL?=INFO
	@export LOG_LEVEL=$(LOG_LEVEL); \
		export TEST_MODE=0; \
		python app.py

prod-ephemeral:
	@echo "Running in production mode with EPHEMERAL (in-memory) DB..."
	@echo "WARNING: Data will NOT be saved."
	@LOG_LEVEL?=INFO
	@export LOG_LEVEL=$(LOG_LEVEL); \
		export TEST_MODE=0; \
		export EPHEMERAL_DB=1; \
		python app.py

test-bot:
	@echo "Running minimal test bot (no DB)..."
	@LOG_LEVEL?=DEBUG
	@export LOG_LEVEL=$(LOG_LEVEL); \
		python test_bot_run.py
