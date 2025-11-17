python -m venv venv
.\venv\Scripts\activate
git init

python -m pip install --upgrade pip

pip install -r requirements.txt

ipython kernel install --user --name venv --display-name="text_analyzer"
python -m spacy download en_core_web_sm

echo $null >> __init__.py


info-density-analyzer/
manifest.json background.js content-script.js
