#!/usr/bin/env python3
"""CDK entry point. Choose the variant and toggles with context, for example:

    cdk synth -c variant=shared -c toggles=sg_change_keeps_connections,shared_nat
"""

import aws_cdk as cdk
from haltai_testbed.config import BedConfig
from haltai_testbed.stacks import build

app = cdk.App()
build(app, BedConfig.from_context(app.node.try_get_context))
app.synth()
