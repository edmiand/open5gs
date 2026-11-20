# device-status-roaming-subscriptions-createDeviceRoamingStatusSubscription
Feature: Device Roaming Status Subscriptions API, v0.9.0 - Operation createDeviceRoamingStatusSubscription

  # Input to be provided by the implementation to the tester
  #
  # Implementation indications:
  # * List of device identifier types which are not supported, among: phoneNumber, networkAccessIdentifier, ipv4Address, ipv6Address
  #
  # Testing assets:
  # * A device object whose roaming status is known by the network when connected.
  # * The known roaming status of the testing device
  # * A sink-url identified as "callbackUrl", which receives notifications
  #
  # References to OAS spec schemas refer to schemas specified in device-roaming-status-subscriptions.yaml

  Background: Common Device Roaming Status Subscriptions setup
    Given the resource "{apiroot}/device-roaming-status-subscriptions/v0.9/subscriptions" as base-url
    And the header "Authorization" is set to a valid access token
    And the header "x-correlator" complies with the schema at "#/components/schemas/XCorrelator"
    And the request body is compliant with the OAS schema at "#/components/schemas/SubscriptionRequest"

##########################
# Happy path scenarios
##########################

  @roaming_status_subscriptions_01.1_sync_creation_2legs
  Scenario Outline: Synchronous subscription creation with 2-legged-access-token
    # Some implementations may only support asynchronous subscription creation
    Given the header "Authorization" is set to a valid access token which does not identify any device
    When the request "createDeviceRoamingStatusSubscription" is sent
    And request property "$.types" is one of the allowed values "<subscription-creation-types>"
    And request property "$.protocol" is equal to "HTTP"
    And a valid phone number identified by "$.config.subscriptionDetail.device.phoneNumber"
    And request property "$.sink" is set to a valid callbackUrl
    Then the response status code is 201
    And the response header "Content-Type" is "application/json"
    And the response header "x-correlator" has same value as the request header "x-correlator"
    And the response body complies with the OAS schema at "#/components/schemas/Subscription"
    And the response properties "$.types", "$.protocol", "$.sink" and "$.config.subscriptionDetail.device.phoneNumber" are present with the values provided in the request
    And the response property "$.id" is present
    And the response properties "$.startsAt" and "$.expiresAt", if present, have a valid value with date-time format
    And the response property "$.status", if present, has the value "ACTIVATION_REQUESTED", "ACTIVE" or "INACTIVE"

    Examples:
      | subscription-creation-types                                                             |
      | org.camaraproject.device-roaming-status-subscriptions.v0.roaming-status                 |
      | org.camaraproject.device-roaming-status-subscriptions.v0.roaming-on                     |
      | org.camaraproject.device-roaming-status-subscriptions.v0.roaming-off                    |
      | org.camaraproject.device-roaming-status-subscriptions.v0.roaming-change-country         |

  @roaming_status_subscriptions_01.2_sync_creation_3legs
  Scenario Outline: Synchronous subscription creation with 3-legged-access-token
    # Some implementations may only support asynchronous subscription creation
    Given the header "Authorization" is set to a valid access token which identifies a valid device
    When the request "createDeviceRoamingStatusSubscription" is sent
    And request property "$.types" is one of the allowed values "<subscription-creation-types>"
    And request property "$.protocol" is equal to "HTTP"
    And request property "$.sink" is set to a valid callbackUrl
    And request property "$.config.subscriptionDetail.device.phoneNumber" is not present
    Then the response status code is 201
    And the response header "Content-Type" is "application/json"
    And the response header "x-correlator" has same value as the request header "x-correlator"
    And the response body complies with the OAS schema at "#/components/schemas/Subscription"
    And the response properties "$.types", "$.protocol" and "$.sink" are present with the values provided in the request
    And the response property "$.id" is present
    And the response properties "$.startsAt" and "$.expiresAt", if present, have a valid value with date-time format
    And the response property "$.status", if present, has the value "ACTIVATION_REQUESTED", "ACTIVE" or "INACTIVE"
    And the response property "$.config.subscriptionDetail.device" is not present

    Examples:
      | subscription-creation-types                                                             |
      | org.camaraproject.device-roaming-status-subscriptions.v0.roaming-status                 |
      | org.camaraproject.device-roaming-status-subscriptions.v0.roaming-on                     |
      | org.camaraproject.device-roaming-status-subscriptions.v0.roaming-off                    |
      | org.camaraproject.device-roaming-status-subscriptions.v0.roaming-change-country         |

  @roaming_status_subscriptions_02_async_creation
  Scenario Outline: Asynchronous subscription creation with 2- or 3-legged access token
    # Some implementations may only support synchronous subscription creation
    Given a valid target device, identified by either the access token or in the request body
    And the request body is compliant with the OAS schema at "#/components/schemas/SubscriptionRequest"
    When the request "createDeviceRoamingStatusSubscription" is sent
    And request property "$.types" is one of the allowed values "<subscription-creation-types>"
    And request property "$.protocol" is equal to "HTTP"
    And request property "$.sink" is set to a valid callbackUrl
    Then the response status code is 202
    And the response header "Content-Type" is "application/json"
    And the response header "x-correlator" has same value as the request header "x-correlator"
    And the response body complies with the OAS schema at "#/components/schemas/SubscriptionAsync"
    And the response property "$.id" is present

    Examples:
      | subscription-creation-types                                                             |
      | org.camaraproject.device-roaming-status-subscriptions.v0.roaming-status                 |
      | org.camaraproject.device-roaming-status-subscriptions.v0.roaming-on                     |
      | org.camaraproject.device-roaming-status-subscriptions.v0.roaming-off                    |
      | org.camaraproject.device-roaming-status-subscriptions.v0.roaming-change-country         |

  @roaming_status_subscriptions_03_subscription_creation_event_validation
  Scenario: Receive notification for subscription-started event on creation
    Given a valid subscription request body
    When the request "createDeviceRoamingStatusSubscription" is sent
    Then the response code is 201 or 202
    And event notification "subscription-started" is received on callback-url
    And notification body complies with the OAS schema at "#/components/schemas/EventSubscriptionStarted"
    And the notification property "$.type" is equal to "org.camaraproject.device-roaming-status-subscriptions.v0.subscription-started"
    And the notification property "$.data.subscriptionId" is equal to response property "$.id"
    And the notification property "$.data.initiationReason" is equal to "SUBSCRIPTION_CREATED"

  @roaming_status_subscriptions_04_receive_notification_when_roaming_status_changed_to_on
  Scenario: Receive notification for roaming-on event
    Given a valid subscription for that device exists with "subscriptionId" equal to "id"
    And the subscription property "$.types" contains the element "org.camaraproject.device-roaming-status-subscriptions.v0.roaming-on"
    And the subscription property "$.sink" is a valid callback URL
    And the device is not roaming
    When the device changes network and is now roaming
    Then event notification "roaming-on" is sent to the specified callback URL
    And the sink credentials specified when the subscription was created are included
    And notification body complies with the OAS schema at "#/components/schemas/EventRoamingOn"
    And the notification property "$.type" is equal to "org.camaraproject.device-roaming-status-subscriptions.v0.roaming-on"
    And the notification property "$.data.subscriptionId" is equal to "id"

  @roaming_status_subscriptions_05_receive_notification_when_roaming_status_changed_to_off
  Scenario: Receive notification for roaming-off event
    Given a valid subscription for that device exists with "subscriptionId" equal to "id"
    And the subscription property "$.types" contains the element "org.camaraproject.device-roaming-status-subscriptions.v0.roaming-off"
    And the subscription property "$.sink" is a valid callback URL
    And the device is roaming
    When the device changes network and is now not roaming
    Then event notification "roaming-off" is sent to the specified callback URL
    And the sink credentials specified when the subscription was created are included
    And notification body complies with the OAS schema at "#/components/schemas/EventRoamingOff"
    And the notification property "$.type" is equal to "org.camaraproject.device-roaming-status-subscriptions.v0.roaming-off"
    And the notification property "$.data.subscriptionId" is equal to "id"

  @roaming_status_subscriptions_06_receive_notification_when_roaming_status_changed
  Scenario: Receive notification for roaming-status changes
    Given a valid subscription for that device exists with "subscriptionId" equal to "id"
    And the subscription property "$.types" contains the element "org.camaraproject.device-roaming-status-subscriptions.v0.roaming-status"
    And the subscription property "$.sink" is a valid callback URL
    When the device changes its roaming status
    Then event notification "roaming-status" is sent to the specified callback URL
    And the sink credentials specified when the subscription was created are included
    And notification body complies with the OAS schema at "#/components/schemas/EventRoamingStatus"
    And the notification property "$.type" is equal to "org.camaraproject.device-roaming-status-subscriptions.v0.roaming-status"
    And the notification property "$.data.subscriptionId" is equal to "id"

  @roaming_status_subscriptions_07_receive_notification_when_roaming_change_country
  Scenario: Receive notification for roaming-change-country
    Given a valid subscription for that device exists with "subscriptionId" equal to "id"
    And the subscription property "$.types" contains the element "org.camaraproject.device-roaming-status-subscriptions.v0.roaming-change-country"
    And the subscription property "$.sink" is a valid callback URL
    And the device is roaming
    When the device changes to a network in a different country and is still roaming
    Then event notification "roaming-change-country" is sent to the specified callback URL
    And the sink credentials specified when the subscription was created are included
    And notification body complies with the OAS schema at "#/components/schemas/EventRoamingChangeCountry"
    And the notification property "$.type" is equal to "org.camaraproject.device-roaming-status-subscriptions.v0.roaming-change-country"
    And the notification property "$.data.subscriptionId" is equal to "id"
    And the notification property "$.data.countryCode" is present and equal to a valid country code
    And the notification property "$.data.countryName" is present and includes a list of all countries corresponding to the country code

  @roaming_status_subscriptions_08_subscription_expiry
  Scenario: Receive notification for subscription-ended event on expiry
    Given a valid subscription for a device exists with "subscriptionId" equal to "id"
    And the subscription property "$.config.subscriptionExpireTime" is set to a value in the near future
    And the subscription property "$.sink" is a valid callback URL
    When the subscriptionExpireTime is reached
    Then a subscription termination event notification is sent to the callback URL
    And the notification body complies with the OAS schema at "#/components/schemas/EventSubscriptionEnded"
    And the notification property "$.type" is "org.camaraproject.device-roaming-status-subscriptions.v0.subscription-ended"
    And the notification property "$.data.subscriptionId" is equal to "id"
    And the notification property "$.data.terminationReason" is equal to "SUBSCRIPTION_EXPIRED"

  @roaming_status_subscriptions_09_subscription_end_when_max_events
  Scenario: Receive notification for subscription-ended event on max events reached
    Given a valid subscription for a device exists with "subscriptionId" equal to "id"
    And the subscription property "$.config.subscriptionMaxEvents" is set to 1
    And the subscription property "$.sink" is a valid callback URL
    When a single notification corresponding to subscription property "$.types" has been sent to the callback URL
    Then a subscription termination event notification is sent to the callback URL
    And the notification body complies with the OAS schema at "#/components/schemas/EventSubscriptionEnded"
    And the notification property "$.type" is equal to "org.camaraproject.device-roaming-status-subscriptions.v0.subscription-ended"
    And the notification property "$.data.subscriptionId" is equal to "id"
    And the notification request property "$.data.terminationReason" is equal to "MAX_EVENTS_REACHED"

  @roaming_status_subscriptions_10_subscription_creation_initial_event
  Scenario: Receive initial event notification on creation
    Given the API supports initial events to be sent
    And a valid subscription request body with property "$.config.initialEvent" set to true
    When the request "createDeviceRoamingStatusSubscription" is sent
    Then the response code is 201 or 202
    And an event notification of the subscribed type is received on callback-url
    And notification body complies with the OAS schema at "#/components/schemas/CloudEvent"

  @roaming_status_subscriptions_11_create_roaming_status_subscription_sync_with_accesstoken_sink_credential
  Scenario: Create roaming status subscription (sync creation) with ACCESSTOKEN sinkCredential
  # Some implementations may only support asynchronous subscription creation
  # Some implementations may decide to not return the sinkCredential in the response (data minimization principle)
    Given that subscriptions are created synchronously
    And a valid subscription request body
    And the request property "$.sinkCredential.credentialType" is set to "ACCESSTOKEN"
    And the request property "$.sinkCredential.accessTokenType" is set to "bearer"
    And the request property "$.sinkCredential.accessToken" is set to a valid access token
    And the request property "$.sinkCredential.accessTokenExpiresUtc" is set to a valid expiry date in the future
    When the request "createDeviceRoamingStatusSubscription" is sent
    Then the response code is 201
    And the response header "Content-Type" is "application/json"
    And the response header "x-correlator" has the same value as the request header "x-correlator"
    And the response body complies with the OAS schema at "#/components/schemas/Subscription"
    And the response body property "$.sinkCredential.credentialType", if present, is set to value "ACCESSTOKEN"
    And the response body property "$.sinkCredential.accessTokenExpiresUtc", if present, is set to the same value of the request property "$.sinkCredential.accessTokenExpiresUtc"

  @roaming_status_subscriptions_12_create_roaming_status_subscription_sync_with_private_key_jwt_sink_credential_out_of_band_provisioning
  Scenario: Create roaming status subscription (sync creation) with PRIVATE_KEY_JWT sinkCredential, out-of-band provisioning
  # Some implementations may only support asynchronous subscription creation
  # Some implementations may only support out_of_band provisioning
    Given that subscriptions are created synchronously
    And a valid subscription request body
    And the request property "$.sinkCredential.credentialType" is set to "PRIVATE_KEY_JWT"
    When the request "createDeviceRoamingStatusSubscription" is sent
    Then the response code is 201
    And the response header "Content-Type" is "application/json"
    And the response header "x-correlator" has the same value as the request header "x-correlator"
    And the response body complies with the OAS schema at "#/components/schemas/Subscription"

  @roaming_status_subscriptions_13_create_roaming_status_subscription_sync_with_private_key_jwt_sink_credential_in_band_provisioning
  Scenario: Create roaming status subscription (sync creation) with PRIVATE_KEY_JWT sinkCredential, in-band provisioning
  # Some implementations may only support asynchronous subscription creation
  # Some implementations may additionally support in_band provisioning
    Given that subscriptions are created synchronously
    And a valid subscription request body
    And the request property "$.sinkCredential.credentialType" is set to "PRIVATE_KEY_JWT"
    And the request property "$.sinkCredential.clientId" is set to a valid value
    And the request property "$.sinkCredential.tokenUri" is set to a valid value
    When the request "createDeviceRoamingStatusSubscription" is sent
    Then the response code is 201
    And the response header "Content-Type" is "application/json"
    And the response header "x-correlator" has the same value as the request header "x-correlator"
    And the response body complies with the OAS schema at "#/components/schemas/Subscription"
    And the response body property "$.sinkCredential.credentialType" is set to value "PRIVATE_KEY_JWT"
    And the response body property "$.sinkCredential.jwksUri" is set to a valid value

##########################################################
# Error scenarios for management of input parameter device
##########################################################

  @roaming_status_subscriptions_C01.01_device_empty
  Scenario: The device value is an empty object
    Given the header "Authorization" is set to a valid access token which does not identify a single device
    And the request body property "$.config.subscriptionDetail.device" is set to: {}
    When the request "createDeviceRoamingStatusSubscription" is sent
    Then the response status code is 400
    And the response property "$.status" is 400
    And the response property "$.code" is "INVALID_ARGUMENT"
    And the response property "$.message" contains a user friendly text

  @roaming_status_subscriptions_C01.02_device_identifiers_not_schema_compliant
  Scenario Outline: Some device identifier value does not comply with the schema
    Given the header "Authorization" is set to a valid access token which does not identify a single device
    And the request body property "<device_identifier>" does not comply with the OAS schema at "<oas_spec_schema>"
    When the request "createDeviceRoamingStatusSubscription" is sent
    Then the response status code is 400
    And the response property "$.status" is 400
    And the response property "$.code" is "INVALID_ARGUMENT"
    And the response property "$.message" contains a user friendly text

    Examples:
      | device_identifier                                          | oas_spec_schema                              |
      | $.config.subscriptionDetail.device.phoneNumber             | #/components/schemas/PhoneNumber             |
      | $.config.subscriptionDetail.device.ipv4Address             | #/components/schemas/DeviceIpv4Address       |
      | $.config.subscriptionDetail.device.ipv6Address             | #/components/schemas/DeviceIpv6Address       |
      | $.config.subscriptionDetail.device.networkAccessIdentifier | #/components/schemas/NetworkAccessIdentifier |

  # This scenario may happen e.g. with 2-legged access tokens, which do not identify a single device.
  @roaming_status_subscriptions_C01.03_device_not_found
  Scenario: Some identifier cannot be matched to a device
    Given the header "Authorization" is set to a valid access token which does not identify a single device
    And the request body property "$.config.subscriptionDetail.device" is compliant with the schema but does not identify a device whose connectivity is managed by the API provider
    When the request "createDeviceRoamingStatusSubscription" is sent
    Then the response status code is 404
    And the response property "$.status" is 404
    And the response property "$.code" is "IDENTIFIER_NOT_FOUND"
    And the response property "$.message" contains a user friendly text

  @roaming_status_subscriptions_C01.04_unnecessary_device
  Scenario: Device not to be included when it can be deduced from the access token
    Given the header "Authorization" is set to a valid access token identifying a device
    And the request body property "$.config.subscriptionDetail.device" is also set to a valid device, which may or may not be the same device
    When the request "createDeviceRoamingStatusSubscription" is sent
    Then the response status code is 422
    And the response property "$.status" is 422
    And the response property "$.code" is "UNNECESSARY_IDENTIFIER"
    And the response property "$.message" contains a user-friendly text

  @roaming_status_subscriptions_C01.05_missing_device
  Scenario: Device not included and cannot be deduced from the access token
    Given the header "Authorization" is set to a valid access token which does not identify a single device
    And the request body property "$.config.subscriptionDetail.device" is not included
    When the request "createDeviceRoamingStatusSubscription" is sent
    Then the response status code is 422
    And the response property "$.status" is 422
    And the response property "$.code" is "MISSING_IDENTIFIER"
    And the response property "$.message" contains a user-friendly text

  @roaming_status_subscriptions_C01.06_unsupported_device
  Scenario: None of the provided device identifiers is supported by the implementation
    Given that some types of device identifiers are not supported by the implementation
    And the header "Authorization" is set to a valid access token which does not identify a single device
    And the request body property "$.config.subscriptionDetail.device" only includes device identifiers not supported by the implementation
    When the request "createDeviceRoamingStatusSubscription" is sent
    Then the response status code is 422
    And the response property "$.status" is 422
    And the response property "$.code" is "UNSUPPORTED_IDENTIFIER"
    And the response property "$.message" contains a user-friendly text

  # When the service is only offered to certain types of devices or subscriptions, e.g. IoT, B2C, etc.
  @roaming_status_subscriptions_C01.07_device_not_supported
  Scenario: Service not available for the device
    Given that the service is not available for all devices commercialized by the operator
    And a valid device, identified by the token or provided in the request body, for which the service is not applicable
    When the request "createDeviceRoamingStatusSubscription" is sent
    Then the response status code is 422
    And the response property "$.status" is 422
    And the response property "$.code" is "SERVICE_NOT_APPLICABLE"
    And the response property "$.message" contains a user-friendly text

##################
# Error code 400
##################

  @roaming_status_subscriptions_400.01_create_subscription_with_invalid_parameter
  Scenario: Create subscription with invalid parameter
    Given the request body is not compliant with the schema "#/components/schemas/SubscriptionRequest"
    When the request "createDeviceRoamingStatusSubscription" is sent
    Then the response status code is 400
    And the response property "$.status" is 400
    And the response property "$.code" is "INVALID_ARGUMENT"
    And the response property "$.message" contains a user friendly text

  @roaming_status_subscriptions_400.02_create_subscription_with_invalid_subscription_expire_time
  Scenario: Expiry time in past
    Given the request body is compliant with the schema "#/components/schemas/SubscriptionRequest"
    And the request property "$.config.subscriptionExpireTime" is set to a time in the past
    When the request "createDeviceRoamingStatusSubscription" is sent
    Then the response status code is 400
    And the response property "$.status" is 400
    And the response property "$.code" is "INVALID_ARGUMENT"
    And the response property "$.message" contains a user friendly text

  @roaming_status_subscriptions_400.03_invalid_eventType
  Scenario: Subscription creation with invalid event type
    Given the request body is compliant with the schema "#/components/schemas/SubscriptionRequest"
    And the request body property "$.types" is set to an invalid value
    When the request "createDeviceRoamingStatusSubscription" is sent
    Then the response status code is 400
    And the response property "$.status" is 400
    And the response property "$.code" is "INVALID_ARGUMENT"
    And the response property "$.message" contains a user friendly text

  @roaming_status_subscriptions_400.04_invalid_protocol
  Scenario: Subscription creation with invalid protocol
    Given the request body is compliant with the schema "#/components/schemas/SubscriptionRequest"
    And the request property "$.protocol" is not equal to "HTTP"
    When the request "createDeviceRoamingStatusSubscription" is sent
    Then the response status code is 400
    And the response property "$.status" is 400
    And the response property "$.code" is "INVALID_PROTOCOL"
    And the response property "$.message" contains a user friendly text

  @roaming_status_subscriptions_400.05_create_subscription_with_invalid_credential_type
  Scenario: Subscription creation with invalid credential type
    Given the request body is compliant with the schema "#/components/schemas/SubscriptionRequest"
    And the request property "$.sinkCredential.accessTokenType" is equal to "bearer"
    And the request property "$.sinkCredential.credentialType" is not equal to "ACCESSTOKEN"
    When the request "createDeviceRoamingStatusSubscription" is sent
    Then the response status code is 400
    And the response property "$.status" is 400
    And the response property "$.code" is "INVALID_CREDENTIAL"
    And the response property "$.message" contains a user friendly text

  @roaming_status_subscriptions_400.06_create_subscription_with_invalid_access_token_type
  Scenario: subscription creation with invalid token
    Given the request body is compliant with the schema "#/components/schemas/SubscriptionRequest"
    And the request property "$.sinkCredential.credentialType" is equal to "ACCESSTOKEN"
    And the request property "$.sinkCredential.accessTokenType" is not equal to "bearer"
    When the request "createDeviceRoamingStatusSubscription" is sent
    Then the response status code is 400
    And the response property "$.status" is 400
    And the response property "$.code" is "INVALID_TOKEN"
    And the response property "$.message" contains a user friendly text

  @roaming_status_subscriptions_400.07_create_subscription_with_invalid_sink_url
  Scenario: Subscription creation with invalid url
    Given the request body is compliant with the schema "#/components/schemas/SubscriptionRequest"
    And the request property "$.protocol" is set to "HTTP"
    And the request property "$.sink" is set to "azerty"
    When the request "createDeviceRoamingStatusSubscription" is sent
    Then the response property "$.status" is 400
    And the response property "$.code" is "INVALID_SINK"
    And the response property "$.message" contains a user friendly text

##################
# Error code 401
##################

  @roaming_status_subscriptions_creation_401.01_no_authorization_header
  Scenario: No Authorization header
    Given the header "Authorization" is removed
    And the request body is compliant with the schema "#/components/schemas/SubscriptionRequest"
    When the request "createDeviceRoamingStatusSubscription" is sent
    Then the response status code is 401
    And the response header "Content-Type" is "application/json"
    And the response property "$.status" is 401
    And the response property "$.code" is "UNAUTHENTICATED"
    And the response property "$.message" contains a user friendly text

  @roaming_status_subscriptions_creation_401.02_expired_access_token
  Scenario: Expired access token
    Given the header "Authorization" is set to a previously valid but now expired access token
    And the request body is compliant with the schema "#/components/schemas/SubscriptionRequest"
    When the request "createDeviceRoamingStatusSubscription" is sent
    Then the response status code is 401
    And the response header "Content-Type" is "application/json"
    And the response property "$.status" is 401
    And the response property "$.code" is "UNAUTHENTICATED"
    And the response property "$.message" contains a user friendly text

  @roaming_status_subscriptions_creation_401.03_malformed_access_token
  Scenario: Malformed access token
    Given the header "Authorization" is set to a malformed token
    And the request body is compliant with the schema "#/components/schemas/SubscriptionRequest"
    When the request "createDeviceRoamingStatusSubscription" is sent
    Then the response status code is 401
    And the response header "Content-Type" is "application/json"
    And the response property "$.status" is 401
    And the response property "$.code" is "UNAUTHENTICATED"
    And the response property "$.message" contains a user friendly text

##################
# Error code 403
##################

  @roaming_status_subscriptions_create_403.01_permission_denied
  Scenario: Roaming-on subscription creation without having the required scope
    Given the access token does not include scope "device-roaming-status-subscriptions:org.camaraproject.device-roaming-status-subscriptions.v0.roaming-on:create"
    And the request body is compliant with the schema "#/components/schemas/SubscriptionRequest"
    And the request body property "$.types" is equal to "org.camaraproject.device-roaming-status-subscriptions.v0.roaming-on"
    When the request "createDeviceRoamingStatusSubscription" is sent
    Then the response status code is 403
    And the response property "$.status" is 403
    And the response property "$.code" is "PERMISSION_DENIED"
    And the response property "$.message" contains a user friendly text

  @roaming_status_subscriptions_create_403.02_permission_denied
  Scenario: Roaming-off subscription creation without having the required scope
    Given the access token does not include scope "device-roaming-status-subscriptions:org.camaraproject.device-roaming-status-subscriptions.v0.roaming-off:create"
    And the request body is compliant with the schema "#/components/schemas/SubscriptionRequest"
    And the request body property "$.types" is equal to "org.camaraproject.device-roaming-status-subscriptions.v0.roaming-off"
    When the request "createDeviceRoamingStatusSubscription" is sent
    Then the response status code is 403
    And the response property "$.status" is 403
    And the response property "$.code" is "PERMISSION_DENIED"
    And the response property "$.message" contains a user friendly text

  @roaming_status_subscriptions_create_403.03_permission_denied
  Scenario: Roaming-status subscription creation without having the required scope
    Given the access token does not include scope "device-roaming-status-subscriptions:org.camaraproject.device-roaming-status-subscriptions.v0.roaming-status:create"
    And the request body is compliant with the schema "#/components/schemas/SubscriptionRequest"
    And the request body property "$.types" is equal to "org.camaraproject.device-roaming-status-subscriptions.v0.roaming-status"
    When the request "createDeviceRoamingStatusSubscription" is sent
    Then the response status code is 403
    And the response property "$.status" is 403
    And the response property "$.code" is "PERMISSION_DENIED"
    And the response property "$.message" contains a user friendly text

  @roaming_status_subscriptions_create_403.04_permission_denied
  Scenario: Roaming-change-country subscription creation without having the required scope
    Given the access token does not include scope "device-roaming-status-subscriptions:org.camaraproject.device-roaming-status-subscriptions.v0.roaming-change-country:create"
    And the request body is compliant with the schema "#/components/schemas/SubscriptionRequest"
    And the request body property "$.types" is equal to "org.camaraproject.device-roaming-status-subscriptions.v0.roaming-change-country"
    When the request "createDeviceRoamingStatusSubscription" is sent
    Then the response status code is 403
    And the response property "$.status" is 403
    And the response property "$.code" is "PERMISSION_DENIED"
    And the response property "$.message" contains a user friendly text

  @roaming_status_subscriptions_create_403.05_subscription_mismatch_for_requested_events_subscription
  Scenario: Subscription creation with invalid access token for requested events subscription
    Given the access token includes only a single subscription scope
    And the request body is compliant with the schema "#/components/schemas/SubscriptionRequest"
    And the request body property "$.types" is equal to a valid type other than the event corresponding to the access token scope
    When the request "createDeviceRoamingStatusSubscription" is sent
    Then the response status code is 403
    And the response property "$.status" is 403
    And the response property "$.code" is "SUBSCRIPTION_MISMATCH"
    And the response property "$.message" contains a user friendly text

##################
# Error code 422
##################

  # Note that the test conditions for this test cannot be satisified for the current definition of #/components/schemas/SubscriptionRequest
  @roaming_status_subscriptions_422.01_multi_event_not_supported
  Scenario: Multi-event subscriptions are not supported
    Given a valid 2- or 3-legged access token
    And a request body that is compliant with the OAS schema at "#/components/schemas/SubscriptionRequest"
    And request property "$.types" includes more than one subscription-type
    When the request "createDeviceRoamingStatusSubscription" is sent
    Then the response status code is 422
    And the response property "$.status" is 422
    And the response property "$.code" is "MULTIEVENT_SUBSCRIPTION_NOT_SUPPORTED"
    And the response property "$.message" contains a user friendly text

  @roaming_status_subscriptions_422.02_creation_with_private_key_jwt_not_configured
  Scenario: Private key JWT not configured for subscription creation
    Given the API provider requires the use of a private key JWT mechanism for subscription creation authentication
    And the private key JWT mechanism is not pre-configured in the environment
    And a valid subscription request body with the property "$.sinkCredential.credentialType" set to "PRIVATE_KEY_JWT"
    When the request "createDeviceRoamingStatusSubscription" is sent
    Then the response code is 422
    And the response property "$.status" is 422
    And the response property "$.code" is "PRIVATE_KEY_JWT_NOT_CONFIGURED"
    And the response property "$.message" contains a user friendly text
