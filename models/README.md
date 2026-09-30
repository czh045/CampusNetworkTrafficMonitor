# Local Model Artifacts

This directory is used at runtime for trained model files such as
`traffic_predictor.pkl`. Model binaries are intentionally ignored by Git
because they are environment-specific and may contain locally derived data.

Run the prediction training endpoint as an administrator after collecting
enough historical traffic records to create a fresh local model.
