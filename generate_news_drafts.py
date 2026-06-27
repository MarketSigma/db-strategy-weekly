{
  "name": "DB Strategy Weekly - Approval to Final Send",
  "safe_separation_note": "Separate approval scenario. It only runs from your approval webhook.",
  "modules": [
    {
      "step": 1,
      "module": "Custom webhook",
      "settings": {"expected_query_params": ["decision"]}
    },
    {
      "step": 2,
      "module": "Router",
      "routes": [
        {
          "name": "Approve",
          "condition": "decision equals approve",
          "actions": [
            {
              "module": "GitHub - Create workflow dispatch event",
              "settings": {
                "repo": "db-strategy-weekly",
                "workflow": "final_send_manual.yml",
                "branch": "main",
                "inputs": {"html_file": "out.html"}
              }
            },
            {
              "module": "Email - confirmation to approver",
              "settings": {"to": "YOUR_EMAIL_ONLY", "subject": "DB Strategy Weekly sent", "body": "Approved article has been sent to final recipients."}
            }
          ]
        },
        {
          "name": "Reject",
          "condition": "decision equals reject",
          "actions": [
            {
              "module": "Email - confirmation to approver",
              "settings": {"to": "YOUR_EMAIL_ONLY", "subject": "DB Strategy Weekly rejected", "body": "No final email was sent."}
            }
          ]
        }
      ]
    }
  ]
}
