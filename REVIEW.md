# What
1. Concurrency for upstream calls of the `/recommendations` endpoint not implemented. Was making requests sequentially even though the requirement was to handle the calls concurrently. The other endpoints were supposed to be async as well, but used the requests library.
2. If else ladder in sizing.py thresholds were less than or equals, but the requirements state less than for the described thresholds
3. There was no logic to handle increasing the lambda size if 120% of in use memory was greater than base memory tier.
4. INFRA-2847 stated that p95 duration should be used for conservative billing projections, however average was being used.
5. Fixed a test that was incorrect based on if else ladder being incorrect. The result should have been 256MB instead of 128MB based on the requirements.
6. Noticed that tests only validated the existence of a field in the response, not the value. I see value in having both the fields and values validated, so I added assertions for the values.
7. Semver versioning updated based on this being a non-breaking change.

# How
1. Used `asyncio.gather` as a sync point for the concurrent upstream calls the `/recommendations` endpoint requires. All endpoints were defined as async, but their implementation used the requests library, which is synchronous. One thing of note is that we're swallowing upstream failures for `/recommendations`, the list might not be the expected length. Not sure if that is considered a breaking change for the existing clients.
2. Changed the if else ladder to be less than instead of less than or equal to.
3. Added a boolean to determine if the requirements to bump up lambda size are met. Iterate through the constant list of predetermined sizes until one that is bigger than what's needed is picked as the recommended size.
4. Referenced the ticket and updated the cost equation to use p95 instead of average for duration.
5. Updated the test to reflect the correct expected value based on the requirements. It was 128MB, I updated it to 256MB
6. Added assertions for values to be validated in the existing tests

# Long Term Goals
1. I would like to speak with who provided the business requirements for this function to determine how to handle a function needing more than 3008MB. Is that something that we would look to move a service into an EKS based service or recommend we increase the size futher since Lambdas are allowed up to 10GB of memory? The results of that conversation would lead to additional logic to handle this scenario.
2. Increase testing. Bare minimum I would like to have one case per fork of logic to guarantee all behavior is verified.
3. Add another optional parameter into the `/services` endpoint that would allow you to do additional filtering based on serviceIds. This would be useful for wants to query individual services and not having to call `/services/{serviceId}` multiple times. 
4. Determine if the warnings for using deprecated libraries in testing needs to be addressed in the near future.
5. Look into why `"_x_submission_token": "BEENG-2026-DELTA"` was returned in `/services/svc-003/metrics` call.