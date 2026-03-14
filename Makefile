.PHONY: monthly-shadow-monitor monthly-shadow-check monthly-ai-briefing

monthly-shadow-monitor:
	python3 run_monthly_shadow_monitor.py

monthly-shadow-check:
	python3 -m unittest discover -s tests

monthly-ai-briefing:
	python3 run_monthly_ai_briefing.py
