"""
Reference crops of the RSA5065N's correction badge (firmware 00.03.08, 1024x600 screenshot from :PRIV:SNAP?), box
x 253..297, y 4..27: dark grey fill (50, 50, 50) with a (66, 66, 66) border.

BLANK         "---" in grey: no correction. From a real screenshot of this instrument (2026-11-25, no calibration).
INTERPOLATED  "C ?" in blue: a correction is on, interpolated or taken in other conditions (2026-11-25, live).
ON            "Cor": a correction is on (2026-11-26, live, while :CAL:S11:STATe? read 0).
No "C !" capture exists yet: it is recognised as "clearly not blank" (screen.classify).
Regenerate with Bitmap.crop(...).pack() from a new screenshot.
"""
from ..screen import Bitmap

BOX = (253, 4, 298, 28)
FILL = (50, 50, 50)
BLANK = Bitmap.unpack(45, 24, 'eNrtlk0KgzAQhVUExQOI+HMGNf6AcaGmQheCQi/gqgdwaZc9eR+kSC6gU0oes3iQxXzMDOQZxldJknDOhRC384UubdtGUWQocl2XMYYndq3Qsaoqz/PAYNt2nufXMxwk6A6GOI6pGA4S3ENd17QYEBi6rmPU6vuefBRyLxpDY2iMMzCKotj3N2rbXqofx7v06/q8Zhrz/EBN06L6puHSg0ffxn9gDMPwC18bPlmkMlqMsix93yePPUEQIAemaUpFgkVkWWZZFjAcx4EnicToq4Zz0zQxGRnGxPnCTSKTh2Eo5wB9ANhxiTk=')
INTERPOLATED = Bitmap.unpack(45, 24, 'eNpjYIACWVlZKysrJycnZ9oDoC3W1tbS0tIMSICDg8PIyAgoZURfALTR1NSUi4sL6AYWFhZDQ0P6uwHuEqDtQDfIyMgMlBvgLgGmBzMzs4F1BhAA3WBvb2800MDBwWHAgwISL0PXGeoBsaKNSwR7t4nVL9Lwi4KLa/pEiNfOF+reLJvTamhiQlNnAK1g2vFFqnSKUmyBVOlklYgMuJR8Wq1MfhdQnHvuWaAU7Zyha+/CsPuHalgafmWKCaV8Uw/SzhnyqTUcy24SVCbSulqicgbtnCFVMpF/0h78aoAO4Jl90sDcknbOkMnv5p15Ao8CDb9ozsXXSHIDGc5QDU1l3vLewMIKpzN8I5RjC2ieYY1NgGlDqGsjxL+GpmYGFtbI8loeQch5h3YZVsfRg2/6EcbdP1jXPgGGjGo4iqVi9Qu5Fl2hWylqYGahZ+MADBw0cUMzC31rBzKc4ejoOBiqNmAlC2yVDawzTExMxMTEBrzZIyEhAWwH6uvrD5RLgBFhYGDAxMQEdAY7OzuQPSBNYqC9yI1zRkZGYMhAGmNOtAfANAlsk0tJSUHCAQgAqE2ePQ==')
ON = Bitmap.unpack(45, 24, 'eNpjYIACWVlZKysrJycnZ9oDoC3W1tbS0tIMSICDg8PIyAgoZURfALTR1NSUi4sL6AYWFhZDQ0P6uwHuEqDtQDfIyMgMlBvgLgGmBzMzs4F1BhAA3WBvb2800MDBwWHAgwISL0PRGTpOnhKVMwV7t4m0rlKKKxoQZ6j7xzBt/yTSulopvlg2u0U2t2NAnMGx9AYwKDDF1YKTJCpnAB1mYGYBEZFPrdG3spUsm6ISmU1dZwCjg2H/P31rBzRx+YwG9hV35TKbRFpWcC28bGhiAhQEBhr/pD2SFdOBAUhdZ6gHxDFv/YApzrrumWpoCoTNtfCSYmIZkMG486tCcgUtIkXTJ4Jhz08jY2NkQQNzS2AQ6dk6QrjCHeslK2ZAnKHt5k8LZwDjnXH3D/WAWBRRYxOG3T90nL0hPMG+HdKFvRBn6Lj40CiJijav4Fx0BZhIIFx9KzsgyTftkFTJRCBDz9aJefMbYKDR2hmGZhbAdMi44wvrhhfMW95Llk8FJV1nb+555zmXXGdb+0Q2tx2ikqbOgDrGxBSYGAxNzVCizMIKLdmQWm44OjoOhqoNWMkCW2UD6wwTExMxMbEBb/ZISEgA24H6+voD5RJgRBgYGDAxMQGdwc7ODmQPSJMYaC9y45yRkREYMpDGmBPtATBNAtvkUlJSkHAAAgByLK7d')
