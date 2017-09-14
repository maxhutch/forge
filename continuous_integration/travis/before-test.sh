mkdir -p ~/mdf/credentials
openssl aes-256-cbc -K $encrypted_c6062119b9e4_key -iv $encrypted_c6062119b9e4_iv -in continuous_integration/travis/MDF_Forge_tokens.json.enc -out ~/mdf/credentials/MDF_Forge_tokens.json -d
