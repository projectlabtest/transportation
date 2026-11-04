package com.lhc.transport.driver.data

import okhttp3.Interceptor
import okhttp3.OkHttpClient
import retrofit2.Response
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path
import retrofit2.http.Query
import java.util.concurrent.TimeUnit

class DriverApiService(baseUrl: String, token: String? = null) {
    private val api: DriverApi

    init {
        val normalizedUrl = baseUrl.trim().trimEnd('/') + "/"
        require(normalizedUrl.startsWith("https://", ignoreCase = true)) {
            "The driver app requires an HTTPS server URL."
        }
        val clientBuilder = OkHttpClient.Builder()
            .connectTimeout(20, TimeUnit.SECONDS)
            .readTimeout(20, TimeUnit.SECONDS)
        if (!token.isNullOrBlank()) {
            clientBuilder.addInterceptor(Interceptor { chain ->
                val request = chain.request().newBuilder()
                    .header("Authorization", "Bearer $token")
                    .build()
                chain.proceed(request)
            })
        }
        api = Retrofit.Builder()
            .baseUrl(normalizedUrl)
            .client(clientBuilder.build())
            .addConverterFactory(GsonConverterFactory.create())
            .build()
            .create(DriverApi::class.java)
    }

    suspend fun login(username: String, password: String): Response<LoginResponse> =
        api.login(LoginRequest(username, password))

    suspend fun fetchTrips(): Response<TripsResponse> = api.fetchTrips()

    suspend fun fetchRequesterBookings(): Response<BookingsResponse> =
        api.fetchRequesterBookings()

    suspend fun createBooking(body: BookingRequest): Response<NewBookingResponse> =
        api.createBooking(body)

    suspend fun cancelBooking(bookingId: Int): Response<ActionResponse> =
        api.cancelBooking(bookingId)

    suspend fun fetchCoordinatorBookings(status: String? = null): Response<BookingsResponse> =
        api.fetchCoordinatorBookings(status)

    suspend fun fetchCoordinatorResources(): Response<CoordinatorResources> =
        api.fetchCoordinatorResources()

    suspend fun reviewBooking(
        bookingId: Int,
        body: ReviewRequest
    ): Response<ActionResponse> = api.reviewBooking(bookingId, body)

    suspend fun action(
        bookingId: Int,
        action: String,
        startMileage: Int? = null,
        endMileage: Int? = null
    ): Response<ActionResponse> =
        api.action(bookingId, ActionRequest(action, startMileage, endMileage))

    suspend fun sendGateFix(
        bookingId: Int,
        latitude: Double,
        longitude: Double,
        accuracy: Double
    ): Response<GateResult> =
        api.sendGateFix(bookingId, GateFixRequest(latitude, longitude, accuracy))

    suspend fun logout(): Response<LogoutResponse> = api.logout()
}

interface DriverApi {
    @POST("api/mobile/login")
    suspend fun login(@Body body: LoginRequest): Response<LoginResponse>

    @GET("api/mobile/my-trips")
    suspend fun fetchTrips(): Response<TripsResponse>

    @GET("api/mobile/requester/bookings")
    suspend fun fetchRequesterBookings(): Response<BookingsResponse>

    @POST("api/mobile/requester/bookings")
    suspend fun createBooking(@Body body: BookingRequest): Response<NewBookingResponse>

    @POST("api/mobile/requester/bookings/{bookingId}/cancel")
    suspend fun cancelBooking(@Path("bookingId") bookingId: Int): Response<ActionResponse>

    @GET("api/mobile/coordinator/bookings")
    suspend fun fetchCoordinatorBookings(
        @Query("status") status: String?
    ): Response<BookingsResponse>

    @GET("api/mobile/coordinator/resources")
    suspend fun fetchCoordinatorResources(): Response<CoordinatorResources>

    @POST("api/mobile/coordinator/bookings/{bookingId}/review")
    suspend fun reviewBooking(
        @Path("bookingId") bookingId: Int,
        @Body body: ReviewRequest
    ): Response<ActionResponse>

    @POST("api/mobile/my-trips/{bookingId}/action")
    suspend fun action(
        @Path("bookingId") bookingId: Int,
        @Body body: ActionRequest
    ): Response<ActionResponse>

    @POST("api/mobile/my-trips/{bookingId}/ssd-gate-location")
    suspend fun sendGateFix(
        @Path("bookingId") bookingId: Int,
        @Body body: GateFixRequest
    ): Response<GateResult>

    @POST("api/mobile/logout")
    suspend fun logout(): Response<LogoutResponse>
}
