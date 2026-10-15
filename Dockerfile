# Playwright image: Momondo walls plain HTTP clients and headless browsers, so every
# request goes through a headful Chromium (src/browser.py) on a virtual display.
# The Playwright pin in requirements.txt must match this image's bundled browser.
FROM apify/actor-python-playwright:3.13

COPY --chown=myuser:myuser requirements.txt ./

RUN echo "Python version:" \
 && python --version \
 && echo "Installing dependencies:" \
 && pip install -r requirements.txt \
 && echo "All installed Python packages:" \
 && pip freeze

COPY --chown=myuser:myuser . ./

RUN python3 -m compileall -q src/

# xvfb-run gives the headful browser a display (headless mode is bot-walled).
CMD ["xvfb-run", "-a", "-s", "-ac -screen 0 1920x1080x24+32 -nolisten tcp", "python3", "-m", "src"]
